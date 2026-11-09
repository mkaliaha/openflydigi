#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
# SPDX-License-Identifier: MIT

"""Independent gyro encoder fixtures, lossless edits and guarded transactions."""
from collections import deque
from contextlib import redirect_stdout, redirect_stderr
import io
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from flydigi import gpa6, gpa6_config, gpa6_gyro, gpa6_profile as profile, mapping, identity
from test_apex6_editing import captured_pad

FIXTURE = json.loads((ROOT / 'tests/fixtures/apex6-gyro.json').read_text())


def bare(blob):
    return gpa6_config.unwrap_blob(blob, b'XMUL')


def replace_record(config, raw):
    data, _, extensions = profile._layout(config.blob)
    _, a, b = next(item for item in extensions if item[0] == 5)
    return profile.MappingConfig(gpa6_config.wrap_blob(data[:a] + bytes([5, len(raw)]) + raw + data[b:]))


class GyroTests(unittest.TestCase):
    def setUp(self):
        self.original = gpa6_config.wrap_blob(bytes.fromhex(next(c['before_hex'] for c in FIXTURE['cases'] if c['name'] == '1.9-mode-0-0')))
        self.config = profile.MappingConfig(self.original, cfg_id=2)

    def test_complete_profiles_match_independent_vendor_encoder(self):
        for case in FIXTURE['cases']:
            with self.subTest(case=case['name']):
                c = profile.MappingConfig(bytes.fromhex(case['before_hex']))
                (c.set_motion if case['operation'] == 'motion' else c.set_motion_curve)(**case['args'])
                self.assertEqual(bare(c.blob).hex(), case['after_hex'])

    def test_shared_ids_names_and_native_trigger_keys(self):
        c = self.config
        c.set_motion(mapping.MOTION_RIGHT_STICK, mapping.MOTION_PRESS, ('select', 'lt'))
        self.assertEqual(c.motion()['target'], mapping.MOTION_RIGHT_STICK)
        self.assertEqual(c.motion()['keys'], ('back', 'lt'))
        self.assertEqual(c._motion_record()[0][13:17], bytes([0, 6, 0, 22]))
        c.set_motion(enable_type=mapping.MOTION_CLICK, keys=('thl', 'thr'))
        self.assertEqual(c.motion()['enable_type'], mapping.MOTION_CLICK)
        self.assertEqual(c.motion()['keys'], ('ls', 'rs'))
        self.assertEqual(c._motion_record()[0][13:17], bytes([1, 8, 1, 9]))
        self.assertNotIn('sensitivity', c.motion())
        self.assertNotIn('dead_zone', c.motion())
        self.assertNotIn('use_mode', c.motion())

    def test_reads_copy_noop_and_absent_extension_do_not_rewrite(self):
        for blob in (self.original, bare(self.original), captured_pad().ram(5),
                     bytes.fromhex(next(c['before_hex'] for c in FIXTURE['cases'] if c['name'] == '1.8-missing'))):
            c = profile.MappingConfig(blob, cfg_id=2)
            copy = c.copy()
            copy.motion(); copy.motion_curve(); copy.set_motion(); copy.set_motion_curve()
            self.assertEqual(copy.blob, blob)
            self.assertEqual(copy.cfg_id, 2)
            copy.set_motion(invert_y=True)
            self.assertEqual(c.blob, blob)
        data, _, extensions = profile._layout(self.original)
        c = profile.MappingConfig(data[:extensions[0][1]])
        self.assertFalse(c.motion()['present'])
        c.set_motion(target='right stick', keys='m4')
        self.assertEqual(bare(c.blob)[-2:], b'\xff\0')
        self.assertTrue(c.motion()['present'])

    def test_hidden_slots_reserved_flags_axes_and_unused_curve_bytes_survive(self):
        c = self.config
        raw = bytearray(c._motion_record()[0])
        raw[5:11] = b'\x11\x22\x33\x44\x55\x66'
        raw[11] = 0xa0
        raw[17:21] = bytes([1, 19, 0, 23])
        raw[21:23] = b'\x03\x00'
        c = replace_record(c, raw)
        c.set_motion(invert_x=True, keys=('m1', 'm2'), mouse_window_radius=100)
        after = c._motion_record()[0]
        self.assertEqual(after[5:11], raw[5:11])
        self.assertEqual(after[11], 0xa1)
        self.assertEqual(after[17:23], raw[17:23])
        self.assertIsNone(c.motion()['enable_type'])
        c.set_motion(enable_type='hold')
        self.assertEqual(c.motion()['enable_type'], mapping.MOTION_PRESS)
        self.assertEqual(c.motion()['activation_slots'][2]['key'], 'm4')
        c.set_motion(target='mouse')
        self.assertEqual(c._motion_record()[0][21:23], b'\x01\x02')
        self.assertEqual(c._motion_record()[0][5:11], raw[5:11])

    def test_only_selected_tlv_changes_and_other_layers_are_byte_exact(self):
        c = self.config
        c.set_macro(5, [{'delay':0,'event':1,'key':'x'}], macro_type='press_hold', name='Keep', layer=1)
        c.set_stick_zones('left', [{'start':80,'stop':200,'dir':'up','target':'y'}], layer=1)
        data, _, extensions = profile._layout(c.blob)
        data = data[:-2] + b'\x71\x03xyz' + data[-2:]
        c = profile.MappingConfig(data)
        _, a, b = next(item for item in profile._layout(data)[2] if item[0] == 5)
        c.set_motion(target='left stick', keys='m4')
        after = bare(c.blob)
        self.assertEqual(after[:a], data[:a])
        self.assertEqual(after[b:], data[b:])

    def test_curve_clear_keeps_unused_slots_and_disable_keeps_curve(self):
        c = self.config
        c.set_motion_curve(start=(20, 0), end=(200, 240), points=[(100, 180), (150, 200)])
        before = c._motion_record()[0]
        c.set_motion_curve(points=[])
        self.assertEqual(c._motion_record()[0][5:11], before[5:11])
        curve = c.motion_curve()
        c.set_motion(target='off')
        self.assertEqual(c.motion_curve(), curve)
        c.set_motion(target='mouse')
        self.assertEqual(c.motion_curve()['end'], (255, 51))
        c.set_motion_curve(end=(255, 80))
        c.set_motion(target='mouse')
        self.assertEqual(c.motion_curve()['end'], (255, 80))
        c.set_motion(target='right stick')
        self.assertEqual(c.motion_curve()['end'], (145, 255))

    def test_prefix_first_layout_is_read_and_edited_in_place(self):
        raw = self.config._motion_record()[0]
        c = replace_record(self.config, raw[11:] + raw[:11])
        self.assertEqual(c.motion()['layout'], 'prefix_first')
        c.set_motion(target='right stick', keys=('m4', 'rt'), enable_type='click', invert_x=True)
        c.set_motion_curve(points=[(80, 100)])
        self.assertEqual(c.motion()['layout'], 'prefix_first')
        self.assertEqual(c.motion()['keys'], ('m4', 'rt'))
        self.assertEqual(c.motion_curve()['points'], [(80, 100)])
        self.assertEqual(c._motion_record()[0][:6], bytes([1, 2, 1, 19, 1, 23]))

    def test_invalid_inputs_are_atomic(self):
        bad = [dict(target=True), dict(target=4), dict(target='right'), dict(enable_type=True),
               dict(enable_type=2), dict(enable_type='always'), dict(keys=True), dict(keys=24),
               dict(keys=(None, 'a')), dict(keys=[]), dict(keys=('a',)), dict(keys='home'),
               dict(keys=('a', 'bad')), dict(keys=('a', True)), dict(invert_x=1),
               dict(invert_y='on'), dict(mouse_window_radius=256), dict(mouse_window_radius=-1),
               dict(mouse_window_radius=True), dict(mouse_window_radius=1.5)]
        for args in bad:
            with self.subTest(args=args), self.assertRaises(ValueError):
                self.config.set_motion(**{'target':'mouse', **args})
            self.assertEqual(self.config.blob, self.original)
        for args in (dict(start=(1,)), dict(end=(256, 1)), dict(points=[(1, 1)] * 3),
                     dict(start=(250, 0), end=(100, 255)), dict(points=[(90, 1), (30, 2)]),
                     dict(points=[(True, 0)])):
            with self.subTest(args=args), self.assertRaises(ValueError):
                self.config.set_motion_curve(**args)
            self.assertEqual(self.config.blob, self.original)

    def test_malformed_and_duplicate_records_are_refused_but_other_edits_preserve_them(self):
        raw = bytearray(self.config._motion_record()[0])
        malformed = [raw[:-1], bytes([255] * 24)]
        for value in malformed:
            c = replace_record(self.config, value)
            before = bytes(c.blob)
            with self.assertRaises(gpa6.ProtocolError):
                c.set_motion(target='off')
            self.assertEqual(c.blob, before)
            c.set_activators('m1', [{'type':'simple','target':'x'}])
            self.assertIn('decode_error', profile.describe(c.blob)['motion'])
        data, _, extensions = profile._layout(self.original)
        _, a, b = next(item for item in extensions if item[0] == 5)
        c = profile.MappingConfig(data[:b] + data[a:b] + data[b:])
        with self.assertRaisesRegex(gpa6.ProtocolError, 'duplicate'):
            c.motion()


class TransactionTests(unittest.TestCase):
    @staticmethod
    def edit(blob):
        c = profile.MappingConfig(blob)
        c.set_motion(target='right stick', keys=('m4', 'lt'), enable_type='hold', invert_y=True)
        c.set_motion_curve(end=(200, 200), points=[(100, 50)])
        return c.blob

    def test_write_save_and_exact_restoration(self):
        pad = captured_pad()
        before = {ram: pad.ram(ram) for ram in (1, 4, 5, 6)}
        after = gpa6_config.edit_ram(pad, 5, self.edit, save=True, expected_uid=pad.uid)
        self.assertEqual(after, pad.stored_configs[0][5])
        self.assertTrue(all(pad.ram(ram) == before[ram] for ram in (1, 4, 6)))
        gpa6_config.edit_ram(pad, 5, lambda _: before[5], save=True)
        pad.power_cycle()
        self.assertEqual({ram: pad.ram(ram) for ram in before}, before)

    def test_lost_commit_ack_rolls_back_every_gyro_field(self):
        pad = captured_pad(); before = pad.ram(5)
        chunks = (len(self.edit(before)) + 15) // 16
        pad.faults[0xa4] = deque([None] * (1 + chunks) + ['drop'])
        with self.assertRaises(gpa6.ReplyTimeout):
            gpa6_config.edit_ram(pad, 5, self.edit)
        self.assertEqual(pad.ram(5), before)
        self.assertIsNone(pad.transfer)

    def test_invalid_edit_and_wrong_uuid_never_write(self):
        pad = captured_pad()
        def invalid(blob):
            c = profile.MappingConfig(blob)
            c.set_motion(target='right stick', keys='m4')
            c.set_motion_curve(end=(999, 0))
            return c.blob
        with self.assertRaises(ValueError):
            gpa6_config.edit_ram(pad, 5, invalid)
        with self.assertRaises(identity.WrongDevice):
            gpa6_config.edit_ram(pad, 5, self.edit, expected_uid='f' * 32)
        self.assertFalse(any(command == 0xa4 for command, _ in pad.history))


class CommandTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        directory = tempfile.TemporaryDirectory(prefix="apex6-gyro-tests-")
        cls.addClassCleanup(directory.cleanup)
        cls.mock_bus = str(Path(directory.name) / "bus.json")
        Path(cls.mock_bus).write_text(json.dumps({"hide_real": True,
            "devices": [{"kind": "pad", "code": "k6"}]}))

    def run_command(self, *args):
        return subprocess.run([sys.executable, str(ROOT / 'tools/flydigi-apex6'), '--device', 'mock:pad0', *args],
                              env={**os.environ, 'FLYDIGI_MOCK_BUS':self.mock_bus}, capture_output=True, text=True)

    def test_read_and_every_write_opt_in(self):
        result = self.run_command('gyro')
        self.assertEqual(result.returncode, 0, result.stderr)
        for args in [('right',), ('off',), ('--enable','hold'), ('--key','m4'), ('--key2','none'),
                     ('--invert-x','on'), ('--invert-y','off'), ('--mouse-window-radius','3'),
                     ('--curve-start','0','10'), ('--curve-end','200','255'), ('--point','80','100'),
                     ('--clear-points',), ('--save',)]:
            result = self.run_command('gyro', *args)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn('require --experimental', result.stderr)

    def test_cli_composes_motion_and_curve_in_one_transaction(self):
        result = self.run_command('--experimental','gyro','right','--key','m4','--key2','lt',
                                  '--enable','toggle','--curve-end','200','240','--point','100','120',
                                  '--invert-x','on','--save')
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual(value['keys'], ['m4','lt'])
        self.assertEqual(value['enable_type'], mapping.MOTION_CLICK)
        self.assertEqual(value['curve'], {'start':[0,0],'end':[200,240],'points':[[100,120]]})
        self.assertIn('save acknowledged', result.stderr)

    def test_invalid_composed_cli_edit_does_not_write_and_key2_edits_preserve_primary(self):
        pad = captured_pad()
        main = runpy.run_path(str(ROOT / 'tools/flydigi-apex6'), run_name='gyro_cli_test')['main']
        def run(*args):
            output = io.StringIO()
            with patch.dict(main.__globals__, {'open_selected':lambda _:pad}), \
                    patch.object(sys,'argv',['flydigi-apex6','--experimental','gyro',*args]), \
                    redirect_stdout(output), redirect_stderr(io.StringIO()):
                status = main()
            return status, json.loads(output.getvalue()) if output.getvalue() else None
        before = pad.ram(5)
        with self.assertRaises(ValueError):
            run('right','--key','m4','--curve-end','999','0')
        self.assertEqual(pad.ram(5), before)
        self.assertFalse(any(command == 0xa4 for command, _ in pad.history))
        self.assertEqual(run('right','--key','m4','--key2','lt')[0], 0)
        self.assertEqual(run('--key2','rt')[1]['keys'], ['m4','rt'])
        self.assertEqual(run('--key','m2')[1]['keys'], ['m2','rt'])
        self.assertEqual(run('--key2','none')[1]['keys'], ['m2',None])

    def test_invalid_cli_input_reports_without_traceback(self):
        for args in [('--mouse-window-radius','256'), ('--point','90','0','--point','20','0'),
                     ('--point','3','4','--clear-points'), ('--key','home')]:
            result = self.run_command('--experimental','gyro',*args)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn('Traceback', result.stderr)


if __name__ == '__main__':
    unittest.main()
