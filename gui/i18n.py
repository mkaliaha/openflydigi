# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Minimal `i18n*` functions for the QML engine.

Kirigami and kirigami-addons call KDE's translation functions from QML --
`i18nc`, `i18ndc`, `i18np` and friends. A C++ KDE application gets them by
installing a `KLocalizedContext` on the engine. There is no way to do that from
PySide6: the class lives in KF6's C++ libraries and has no Python binding.

Without them the components do not merely go untranslated, they throw:
`FormTextFieldDelegate` evaluates `i18ndc(...)` inside a `TextMetrics` whose
binding runs whether or not the label it feeds is visible, so every text field
in the application logs `ReferenceError: i18ndc is not defined`.

So the engine gets the functions itself. They do no translation -- they return
the source string with `%1`, `%2` … substituted, which is what an untranslated
KDE application displays anyway. When there is a translation story, this is the
piece that gets replaced.
"""

# Defined in JavaScript rather than Python because QML has to be able to call
# them directly: a Python callable put in a context property is not callable
# from QML, a QJSValue function is.
_SHIM = """
(function () {
    function fill(text, args) {
        var out = String(text);
        for (var i = 0; i < args.length; ++i)
            out = out.replace('%' + (i + 1), args[i]);
        return out;
    }

    // `skip` is how many leading arguments name the domain and the
    // disambiguation context rather than the message itself.
    function singular(skip) {
        return function () {
            var a = Array.prototype.slice.call(arguments);
            return fill(a[skip], a.slice(skip + 1));
        };
    }

    // The plural forms take (…, singular, plural, n, …). English rules: the
    // singular is used for exactly one.
    function plural(skip) {
        return function () {
            var a = Array.prototype.slice.call(arguments);
            var n = a[skip + 2];
            return fill(n === 1 ? a[skip] : a[skip + 1], a.slice(skip + 2));
        };
    }

    return {
        i18n: singular(0),
        i18nd: singular(1),
        i18nc: singular(1),
        i18ndc: singular(2),
        i18np: plural(0),
        i18ndp: plural(1),
        i18ncp: plural(1),
        i18ndcp: plural(2)
    };
})()
"""

# The x-prefixed variants accept KUIT markup in the source string. Nothing here
# uses markup, so they behave the same.
NAMES = ["i18n", "i18nd", "i18nc", "i18ndc",
         "i18np", "i18ndp", "i18ncp", "i18ndcp"]


def install(engine):
    """Put the `i18n*` functions in the engine's root context."""
    shim = engine.evaluate(_SHIM)
    if shim.isError() or not shim.isObject():
        raise RuntimeError(f"could not build the i18n shim: {shim.toString()}")
    context = engine.rootContext()
    for name in NAMES:
        context.setContextProperty(name, shim.property(name))
        context.setContextProperty("x" + name, shim.property(name))
    return shim
