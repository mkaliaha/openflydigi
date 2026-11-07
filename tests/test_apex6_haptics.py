#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
# SPDX-License-Identifier: MIT

"""Native profile feedback: independent web fixtures and guarded device edits."""
from collections import deque
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from flydigi import evdev, evdev_rumble, gpa6, gpa6_config, gpa6_profile, gpa6_haptics as haptics
from test_apex6_editing import captured_pad
from test_apex6_response import extensions, bare

FIXTURES = json.loads((ROOT / 'tests/fixtures/apex6-haptics.json').read_text())


class HapticTests(unittest.TestCase):
    def config(self):
        pad = captured_pad()
        return haptics.HapticConfig(pad.ram(5), pad.ram(6), haptic_grip=True)

    def test_five_levels_match_independent_vendor_full_profile_encoder(self):
        for case in FIXTURES['cases']:
            with self.subTest(target=case['target'], version=case['version'], level=case['level']):
                config = haptics.HapticConfig(bytes.fromhex(case['before']['wrapped_hex']),
                    captured_pad().ram(6), haptic_grip=case.get('haptic', False))
                config.set_vibration(case['target'], enabled=case.get('enabled'), level=case['level'])
                self.assertEqual(config.mapping.blob.hex(), case['after']['wrapped_hex'])

    def test_read_and_empty_edit_are_lossless(self):
        config = self.config()
        copied = config.copy()
        for target in ('grips', 'left', 'right'):
            copied.vibration(target)
            copied.set_vibration(target)
        self.assertEqual(copied.mapping.blob, config.mapping.blob)
        self.assertEqual(copied.motors.blob, config.motors.blob)
        self.assertEqual(copied._steps, [])

    def test_invalid_controls_do_not_write(self):
        pad = captured_pad()
        for args in ({'level':0}, {'level':6}, {'level':True}, {'level':2.5}, {'enabled':1}):
            with self.subTest(args=args), self.assertRaises(ValueError):
                haptics.edit(pad, lambda c: c.set_vibration('left', **args))
        self.assertFalse(any(cmd in (0x53, 0xa4) for cmd, _ in pad.history))

    def test_unrelated_tlvs_flags_reserved_and_inactive_points_preserved(self):
        config = self.config()
        config.mapping._set_vibration_curve('grip','left',points=[(80,30),(170,190)])
        data, _, records = gpa6_profile._layout(config.mapping.blob)
        start = next(start for key,start,_ in records if key == 8)
        raw = bytearray(data)
        raw[start+2] |= 0xe0
        raw[start+12:start+14] = b'xy'
        config.mapping = gpa6_profile.MappingConfig(gpa6_config.wrap_blob(raw))
        before = extensions(config.mapping.blob)
        config.set_vibration('grips', level=2)
        after = extensions(config.mapping.blob)
        self.assertEqual({k:v for k,v in before.items() if k not in (8,9)},
                         {k:v for k,v in after.items() if k not in (8,9)})
        self.assertEqual(after[8][2] & 0xe0, 0xe0)
        self.assertEqual(after[8][-2:], b'xy')
        self.assertEqual(after[8][8:12], before[8][8:12])

    def test_duplicate_or_malformed_selected_tlv_refused(self):
        config=self.config()
        for tail in (b'\x08\x01x', b'\x08\x0c'+bytes(12)+b'\x08\x0c'+bytes(12)):
            raw,_,records=gpa6_profile._layout(config.mapping.blob)
            base=raw[:records[0][1]]
            broken=haptics.HapticConfig(gpa6_config.wrap_blob(base+tail+b'\xff\0'),config.motors.blob)
            with self.assertRaises(gpa6.ProtocolError): broken.set_vibration('grips',level=1)

    def test_motor_switches_independent_and_preserve_other_bytes(self):
        config=self.config();before=bytes(config.motors.blob)
        config.set_vibration('left',enabled=False)
        self.assertEqual(config.motors.blob[1:12],bytes(11))
        self.assertEqual(config.motors.blob[12:],before[12:])
        config.set_vibration('left',enabled=True)
        self.assertEqual(config.motors.blob,before)

    def test_opaque_selected_motor_bytes_are_not_discarded(self):
        config=self.config();config.motors.blob[11]=42
        with self.assertRaises(gpa6.ProtocolError): config.set_vibration('left',enabled=False)
        config.set_vibration('right',enabled=False)
        self.assertEqual(config.motors.blob[11],42)

    def test_capabilities_gate_trigger_edits(self):
        pad=captured_pad();pad.features &= ~16
        with self.assertRaises(gpa6.ProtocolError):
            haptics.edit(pad,lambda c:c.set_vibration('left',level=2))
        self.assertFalse(any(cmd in (0xa4,0x53) for cmd,_ in pad.history))

    def test_guarded_apply_and_restore_both_domains(self):
        pad=captured_pad();before={ram:pad.ram(ram) for ram in (1,4,5,6)}
        result=haptics.edit(pad,lambda c:c.set_vibration('left',enabled=False,level=2),save=True)
        self.assertEqual(pad.ram(5),result.mapping.blob)
        self.assertEqual(pad.ram(6),result.motors.blob)
        self.assertEqual(pad.ram(1),before[1]);self.assertEqual(pad.ram(4),before[4])
        self.assertIn((0x53,b'\0\0\0'),pad.history)
        self.assertFalse(any(cmd==0x53 and payload[0]==1 for cmd,payload in pad.history))

    def test_lost_motor_ack_rolls_back_exactly(self):
        pad=captured_pad();before=pad.ram(6);pad.faults[0x53]=deque(['drop'])
        with self.assertRaises(gpa6.ReplyTimeout):
            haptics.edit(pad,lambda c:c.set_vibration('left',enabled=False))
        self.assertEqual(pad.ram(6),before)

    def test_mapping_failure_rolls_back_prior_motor_step(self):
        pad=captured_pad();before={ram:pad.ram(ram) for ram in (5,6)}
        pad.faults[0xa4]=deque(['error'])
        with self.assertRaises(gpa6.ProtocolError):
            haptics.edit(pad,lambda c:c.set_vibration('left',enabled=False,level=2))
        self.assertEqual({ram:pad.ram(ram) for ram in (5,6)},before)

    def test_concurrent_motor_edit_is_not_overwritten_on_failure(self):
        pad=captured_pad();send=pad.send
        def concurrent(report,**kwargs):
            result=send(report,**kwargs)
            if report[3]==0x53:
                pad.maps[0][2]=99
            return result
        with patch.object(pad,'send',side_effect=concurrent),self.assertRaisesRegex(gpa6.ProtocolError,'rollback incomplete'):
            haptics.edit(pad,lambda c:c.set_vibration('left',enabled=False))
        self.assertEqual(pad.maps[0][2],99)
        self.assertEqual(sum(cmd==0x53 for cmd,_ in pad.history),1)

    def test_profile_switch_and_uuid_change_prevent_restore_to_wrong_target(self):
        for change in ('slot','uid'):
            pad=captured_pad();send=pad.send;other=pad.ram(6)
            def switched(report,**kwargs):
                result=send(report,**kwargs)
                if report[3]==0x53:
                    if change=='slot':pad.change_profile(1)
                    else:pad.uid='ff'*16
                return result
            with patch.object(pad,'send',side_effect=switched),self.assertRaisesRegex(gpa6.ProtocolError,'rollback incomplete'):
                haptics.edit(pad,lambda c:c.set_vibration('left',enabled=False))
            self.assertEqual(sum(cmd==0x53 for cmd,_ in pad.history),1)

    def test_external_mapping_change_between_check_and_transfer_is_refused(self):
        pad=captured_pad();original=gpa6.Protocol.write_ram
        def racing(protocol,ram,data,*,expected):
            pad.configs[0][5] += b'changed'
            return original(protocol,ram,data,expected=expected)
        with patch.object(gpa6.Protocol,'write_ram',racing), self.assertRaises(gpa6.ProtocolError):
            haptics.edit(pad,lambda c:c.set_vibration('grips',level=1))
        self.assertTrue(pad.ram(5).endswith(b'changed'))

    def test_cli_requires_opt_in_and_accepts_vendor_controls(self):
        env={**os.environ,'FLYDIGI_MOCK_BUS':'pad:k6'}
        def run(*args):return subprocess.run([sys.executable,str(ROOT/'tools/flydigi-apex6'),*args],env=env,capture_output=True,text=True)
        result=run('vibration');self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('grips',json.loads(result.stdout)['vibration'])
        self.assertNotEqual(run('vibration','left','--enabled','off').returncode,0)
        self.assertNotEqual(run('--experimental','vibration','--level','2').returncode,0)
        result=run('--experimental','vibration','grips','--level','2')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout)['level'],2)

    def test_all_vendor_scene_parameters_and_decode(self):
        names=list(haptics.SCENES)
        for case in FIXTURES['scenes']:
            with self.subTest(case=case):
                config=self.config()
                config.set_trigger_scene('left',names[case['id']],travel=case['travel'],frequency=case['frequency'])
                self.assertEqual(config.motors.trigger('left')['params'].hex(),case['params_hex'])
                parsed=config.trigger_scene('left')
                self.assertEqual((parsed['scene'],parsed['travel'],parsed['frequency']),
                    (names[case['id']],case['travel'],case['frequency']))

    def test_scene_preserves_input_curves_zones_and_opposite_side(self):
        config=self.config();config.mapping.set_trigger_zones('left',[{'start':70,'stop':255,'target':'a'}])
        config.mapping.set_response_curve('trigger','left',end=(220,255))
        before=extensions(config.mapping.blob);motors=bytes(config.motors.blob)
        config.set_trigger_scene('left','rifle')
        after=extensions(config.mapping.blob)
        self.assertEqual({k:v for k,v in after.items() if k!=6},{k:v for k,v in before.items() if k!=6})
        self.assertEqual(config.mapping.trigger_zones('left'),
                         [{'start':70,'stop':255,'target':{'type':'btn_gamepad','id':'a'}}])
        self.assertEqual(config.motors.blob[12:],motors[12:])
        self.assertEqual(config.mapping.vibration_curve('trigger','left')['curve']['end'],(255,0))

    def test_vendor_normal_mode_resets_only_selected_base_trigger_input(self):
        config=self.config()
        config.mapping.ensure_layer(1)
        for layer in (0,1):config.mapping.set_trigger_zones('left',[{'start':100,'stop':255,'target':'b'}],layer=layer)
        config.mapping.set_response_curve('trigger','left',end=(100,200))
        config.set_trigger_scene('left','pistol')
        config.set_trigger_scene('left','normal')
        self.assertEqual(config.trigger_scene('left')['mode'],'normal')
        self.assertEqual(config.mapping.response_curve('trigger','left'),{'start':(0,0),'end':(255,255),'points':[]})
        self.assertEqual(config.mapping.trigger_zones('left'),[])
        self.assertEqual(len(config.mapping.trigger_zones('left',layer=1)),1)

    def test_scene_and_vibration_conflicts_follow_order(self):
        config=self.config();config.set_trigger_scene('left','pistol')
        self.assertEqual([s[0] for s in config._steps],[5,6])
        config._steps=[];config.set_vibration('left',enabled=True,level=3)
        self.assertEqual([s[0] for s in config._steps],[6,6,5])
        self.assertEqual(config.trigger_scene('left')['mode'],'vibration')
        self.assertEqual(config.vibration('left')['curve']['end'],(255,153))

    def test_scene_adjustments_keep_each_sides_scene_and_other_fields(self):
        config=self.config();config.set_trigger_scene('left','rifle');config.set_trigger_scene('right','machine-gun')
        mapping=bytes(config.mapping.blob)
        config.set_trigger_scene('both',travel=3,frequency=1)
        self.assertEqual(config.mapping.blob,mapping)
        self.assertEqual(config.trigger_scene('left')['scene'],'rifle')
        self.assertEqual(config.trigger_scene('right')['scene'],'machine-gun')
        self.assertEqual(config.trigger_scene('right')['frequency'],1)
        config.set_trigger_scene('left','sword')
        with self.assertRaises(ValueError):config.set_trigger_scene('left',frequency=2)

    def test_unsupported_scene_adjustments_are_rejected_before_writes(self):
        pad=captured_pad()
        for args in ({'scene':'invented'},{'scene':'pistol','frequency':1},{'scene':'normal','travel':1},
                     {'scene':'rifle','travel':True},{'travel':2}):
            with self.assertRaises(ValueError):haptics.edit(pad,lambda c:c.set_trigger_scene('both',**args))
        self.assertFalse(any(cmd in (0xa4,0x53) for cmd,_ in pad.history))

    def test_second_scene_motor_failure_rolls_back_both_domains(self):
        pad=captured_pad();before={ram:pad.ram(ram) for ram in (5,6)}
        pad.faults[0x53]=deque([None,'drop'])
        with self.assertRaises(gpa6.ReplyTimeout):
            haptics.edit(pad,lambda c:c.set_trigger_scene('both','rifle'))
        self.assertEqual({ram:pad.ram(ram) for ram in (5,6)},before)

    def test_scene_cli(self):
        env={**os.environ,'FLYDIGI_MOCK_BUS':'pad:k6'}
        def run(*args):return subprocess.run([sys.executable,str(ROOT/'tools/flydigi-apex6'),*args],env=env,capture_output=True,text=True)
        self.assertNotEqual(run('trigger-scene','left','--scene','rifle').returncode,0)
        result=run('--experimental','trigger-scene','both','--scene','rifle','--travel','3','--frequency','1')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout)['right']['frequency'],1)
        self.assertNotEqual(run('--experimental','trigger-scene','left','--scene','sword','--frequency','3').returncode,0)

    def test_vibration_test_routes_same_device_and_stops_without_writes(self):
        pad=captured_pad();values={evdev.ABS_Z:0,evdev.ABS_RZ:.3}
        reader=SimpleNamespace(abs={evdev.ABS_Z:1,evdev.ABS_RZ:1},axis=values.__getitem__)
        from unittest.mock import Mock
        reader.close=Mock();output=Mock()
        with patch.object(evdev,'usb_device_of_hidraw',return_value='/selected/usb'), \
             patch.object(evdev,'find_device',return_value=('/selected/event','gamepad')) as find, \
             patch.object(evdev,'Reader',return_value=reader), \
             patch.object(evdev_rumble,'RumbleOutput',return_value=output), \
             patch.object(haptics.threading,'Thread'), \
             patch.object(haptics.select,'select',return_value=([],[],[])):
            with haptics.VibrationTest(pad) as test:
                test._tick_output()
                self.assertTrue(test.tick());output.play.assert_called_with(65535,65535,150)
                test._tick_output();test._tick_output()
                self.assertEqual(output.play.call_count,3,'right-only holds must refresh the effect')
                values[evdev.ABS_RZ]=0
                test._tick_output()
                self.assertFalse(test.tick());output.stop.assert_called_once()
                values[evdev.ABS_Z]=.3
                test._tick_output()
                values[evdev.ABS_RZ]=.3
                test._tick_output()
                values[evdev.ABS_Z]=0
                test._tick_output()
                self.assertEqual(output.play.call_count,6)
                self.assertTrue(all(call.args==(65535,65535,150) for call in output.play.call_args_list))
                values[evdev.ABS_RZ]=0
                test._tick_output()
                self.assertEqual(output.stop.call_count,2)
            output.close.assert_called_once();reader.close.assert_called_once()
            find.assert_called_with(axes=True,usb_root='/selected/usb')
        self.assertFalse(any(cmd in (0xa4,0x53,0x57) for cmd,_ in pad.history))

    def test_vibration_test_stops_and_closes_on_profile_change(self):
        from unittest.mock import Mock
        pad=captured_pad();reader=Mock();reader.abs={evdev.ABS_Z:1,evdev.ABS_RZ:1};output=Mock()
        with patch.object(evdev,'usb_device_of_hidraw',return_value='/selected/usb'), \
             patch.object(evdev,'find_device',return_value=('/selected/event','gamepad')), \
             patch.object(evdev,'Reader',return_value=reader), \
             patch.object(evdev_rumble,'RumbleOutput',return_value=output), \
             patch.object(haptics.threading,'Thread'):
            with self.assertRaises(gpa6.ProtocolError):
                with haptics.VibrationTest(pad) as test:
                    pad.change_profile(1);test.tick()
            output.close.assert_called_once();reader.close.assert_called_once()

    def test_status_queries_wait_for_release_while_rumble_is_refreshed(self):
        from unittest.mock import Mock
        import threading
        pad=captured_pad()
        state={'held':True}
        refreshed=threading.Event();released=threading.Event()
        reader=SimpleNamespace(abs={evdev.ABS_Z:1,evdev.ABS_RZ:1},
                               axis=lambda code:.3 if state['held'] else 0,close=Mock())
        output=Mock()
        def play(*args):
            if output.play.call_count>=2:refreshed.set()
        output.play.side_effect=play
        output.stop.side_effect=released.set
        with patch.object(evdev,'usb_device_of_hidraw',return_value='/selected/usb'), \
             patch.object(evdev,'find_device',return_value=('/selected/event','gamepad')), \
             patch.object(evdev,'Reader',return_value=reader), \
             patch.object(evdev_rumble,'RumbleOutput',return_value=output), \
             patch.object(haptics.select,'select',return_value=([],[],[])):
            with haptics.VibrationTest(pad) as test:
                self.assertTrue(refreshed.wait(2),'held effect must be refreshed independently')
                with patch.object(test.protocol,'uid',return_value=pad.uid) as query:
                    self.assertTrue(test.tick())
                    self.assertTrue(test.tick())
                    query.assert_not_called()
                    state['held']=False
                    self.assertTrue(released.wait(2),'release must stop output without a HID query')
                    self.assertFalse(test.tick())
                    query.assert_called_once()
                self.assertGreaterEqual(output.play.call_count,2)
            self.assertFalse(test._worker.is_alive())
            output.close.assert_called_once()

    def test_background_output_error_reaches_owner_and_closes_resources(self):
        from unittest.mock import Mock
        import threading
        pad=captured_pad();failed=threading.Event()
        reader=SimpleNamespace(abs={evdev.ABS_Z:1,evdev.ABS_RZ:1},axis=lambda code:.3,close=Mock())
        output=Mock()
        def fail(*args):failed.set();raise OSError('output disconnected')
        output.play.side_effect=fail
        with patch.object(evdev,'usb_device_of_hidraw',return_value='/selected/usb'), \
             patch.object(evdev,'find_device',return_value=('/selected/event','gamepad')), \
             patch.object(evdev,'Reader',return_value=reader), \
             patch.object(evdev_rumble,'RumbleOutput',return_value=output), \
             patch.object(haptics.select,'select',return_value=([],[],[])):
            with self.assertRaisesRegex(OSError,'output disconnected'):
                with haptics.VibrationTest(pad) as test:
                    self.assertTrue(failed.wait(2))
                    test._worker.join(2)
                    test.tick()
            output.close.assert_called_once();reader.close.assert_called_once()

    def test_rumble_output_uses_finite_effect_and_stops_before_erasing(self):
        import ctypes
        import struct
        events=[]
        def ioctl(fd,command,*args):
            events.append(('ioctl',command))
            if command & 255 == 0x80:
                effect=evdev_rumble._Effect.from_buffer(args[0])
                self.assertEqual((effect.type,effect.replay[0],effect.u.rumble.strong,effect.u.rumble.weak),
                                 (0x50,150,65535,32768))
                effect.id=7
        with patch.object(evdev_rumble.os,'open',return_value=22), \
             patch.object(evdev_rumble.fcntl,'ioctl',side_effect=ioctl), \
             patch.object(evdev_rumble.os,'write',side_effect=lambda fd,data:events.append(('event',struct.unpack('llHHi',data)[-1]))), \
             patch.object(evdev_rumble.os,'close') as close:
            output=evdev_rumble.RumbleOutput('/selected/event')
            output.play(65535,32768);output.play(65535,32768);output.close()
            self.assertEqual([e for e in events if e[0]=='event'],[('event',1),('event',1),('event',0)])
            self.assertEqual(sum(e[0]=='ioctl' and e[1] & 255 == 0x80 for e in events),1)
            self.assertEqual(events[-1][1] & 255,0x81);close.assert_called_once_with(22)


if __name__=='__main__':unittest.main()
