import os
import sys
import threading
import time

from . import engine


BAR_WIDTH = 28
NAME_WIDTH = 28


def _utf8_capable():
    enc = (sys.stderr.encoding or '').lower()
    if 'utf' in enc:
        return True
    for var in ('LC_ALL', 'LC_CTYPE', 'LANG'):
        if 'utf' in os.environ.get(var, '').lower():
            return True
    return False


def _color_supported():
    if os.environ.get('NO_COLOR'):
        return False
    return sys.stderr.isatty()


class _Style:
    def __init__(self, color, utf8):
        self.color = color
        if utf8:
            self.fill_char = '█'      # █
            self.empty_char = '░'     # ░
            self.bullet = '•'         # •
            self.check = '✓'          # ✓
            self.cross = '✗'          # ✗
            self.warn = '⚠'           # ⚠
        else:
            self.fill_char = '#'
            self.empty_char = '-'
            self.bullet = '*'
            self.check = '[OK]'
            self.cross = '[FAIL]'
            self.warn = '[WARN]'

    def red(self, s):
        return '\x1b[31m' + s + '\x1b[0m' if self.color else s

    def green(self, s):
        return '\x1b[32m' + s + '\x1b[0m' if self.color else s

    def orange(self, s):
        # 256-color orange (208); most modern terminals support this. Fall back
        # to yellow on 8-color terms is automatic — \x1b[38;5;Nm is widely
        # honored on RHEL/V/IS default terminals.
        return '\x1b[38;5;208m' + s + '\x1b[0m' if self.color else s

    def cyan(self, s):
        return '\x1b[36m' + s + '\x1b[0m' if self.color else s

    def dim(self, s):
        return '\x1b[2m' + s + '\x1b[0m' if self.color else s

    def bold(self, s):
        return '\x1b[1m' + s + '\x1b[0m' if self.color else s


def make_style(color=None, utf8=None):
    if color is None:
        color = _color_supported()
    if utf8 is None:
        utf8 = _utf8_capable()
    return _Style(color, utf8)


class ProgressReporter(engine.NullProgress):
    """TTY-aware pacman-style progress reporter writing to stderr."""

    def __init__(self, style=None, stream=None):
        self.style = style or make_style()
        self.stream = stream if stream is not None else sys.stderr
        self._lock = threading.Lock()
        self._spinner_stop = None
        self._spinner_thread = None
        self._spinner_pos = 0
        self._idx = 0
        self._total_phases = 0
        self._name = ''
        self._total = None
        self._current = 0
        self._active = False

    # ---- engine progress API ----
    def phase(self, idx, total_phases, name, total_steps=None):
        self._stop_spinner()
        self._idx = idx
        self._total_phases = total_phases
        self._name = name
        self._total = total_steps
        self._current = 0
        self._active = True
        if total_steps is None:
            self._start_spinner()
        else:
            self._redraw_definite()

    def tick(self, n=1):
        if not self._active or self._total is None:
            return
        self._current = min(self._current + n, self._total)
        self._redraw_definite()

    def done(self):
        self._stop_spinner()
        if not self._active:
            return
        # Final line — full bar.
        self._draw_line(filled=BAR_WIDTH, count_str=self._final_count_str(), newline=True)
        self._active = False

    # ---- internals ----
    def _final_count_str(self):
        if self._total is None:
            return ''
        return '{}/{}'.format(self._total, self._total)

    def _redraw_definite(self):
        filled = int(round(BAR_WIDTH * self._current / self._total)) if self._total else 0
        count = '{}/{}'.format(self._current, self._total)
        self._draw_line(filled=filled, count_str=count, newline=False)

    def _draw_line(self, filled, count_str, newline):
        s = self.style
        bar_inner = s.fill_char * filled + s.empty_char * (BAR_WIDTH - filled)
        phase_label = '({}/{})'.format(self._idx, self._total_phases)
        name = self._name.ljust(NAME_WIDTH)
        line = '{phase} {name} [{bar}]'.format(
            phase=s.cyan(phase_label),
            name=name,
            bar=s.green(bar_inner),
        )
        if count_str:
            line += ' ' + s.dim(count_str)
        with self._lock:
            self.stream.write('\r\x1b[K' + line)
            if newline:
                self.stream.write('\n')
            self.stream.flush()

    def _start_spinner(self):
        self._spinner_pos = 0
        self._spinner_stop = threading.Event()

        def loop():
            # Knight-rider chunk that bounces inside the bar.
            chunk = 4
            pos = 0
            direction = 1
            while not self._spinner_stop.is_set():
                bar = [self.style.empty_char] * BAR_WIDTH
                for i in range(chunk):
                    p = pos + i
                    if 0 <= p < BAR_WIDTH:
                        bar[p] = self.style.fill_char
                self._draw_indeterminate(''.join(bar))
                pos += direction
                if pos + chunk >= BAR_WIDTH or pos <= 0:
                    direction = -direction
                if self._spinner_stop.wait(0.08):
                    break

        self._spinner_thread = threading.Thread(target=loop, daemon=True)
        self._spinner_thread.start()

    def _draw_indeterminate(self, bar_inner):
        s = self.style
        phase_label = '({}/{})'.format(self._idx, self._total_phases)
        name = self._name.ljust(NAME_WIDTH)
        line = '{phase} {name} [{bar}]'.format(
            phase=s.cyan(phase_label),
            name=name,
            bar=s.green(bar_inner),
        )
        with self._lock:
            self.stream.write('\r\x1b[K' + line)
            self.stream.flush()

    def _stop_spinner(self):
        if self._spinner_thread is not None:
            self._spinner_stop.set()
            self._spinner_thread.join()
            self._spinner_thread = None
            self._spinner_stop = None


class _PlainProgress(engine.NullProgress):
    """Used when not a TTY but we still want a one-line-per-phase trace."""
    def __init__(self, stream=None):
        self.stream = stream if stream is not None else sys.stderr
        self._idx = 0

    def phase(self, idx, total_phases, name, total_steps=None):
        self._idx = idx
        self.stream.write('({}/{}) {}\n'.format(idx, total_phases, name))
        self.stream.flush()

    def tick(self, n=1):
        pass

    def done(self):
        pass


def make_progress(style=None, enabled=None, stream=None):
    """Return a progress reporter appropriate for the current environment.

    Honors stderr TTY detection unless `enabled` is True/False explicitly.
    """
    stream = stream if stream is not None else sys.stderr
    if enabled is None:
        enabled = stream.isatty()
    if not enabled:
        return _PlainProgress(stream=stream)
    return ProgressReporter(style=style, stream=stream)


def _format_elapsed(seconds):
    if seconds < 10:
        return '{:.1f}s'.format(seconds)
    return '{:.0f}s'.format(seconds)


def _format_finding(f, style, verbose):
    parts = [style.bold(f.name)]
    if f.driver:
        parts.append('— driver ' + f.driver)
    line = '    ' + style.bullet + ' ' + ' '.join(parts)
    extras = []
    if f.slot:
        extras.append('slot ' + f.slot)
    if verbose:
        if f.modalias:
            extras.append('modalias ' + f.modalias)
        if f.sysfs_path:
            extras.append('sysfs ' + f.sysfs_path)
    if extras:
        line += '\n      ' + style.dim(' · '.join(extras))
    return line


def render_summary(result, style=None, verbose=False, stream=None):
    """Render the human-readable summary to `stream` (default stdout)."""
    if style is None:
        style = make_style()
    if stream is None:
        stream = sys.stdout

    write = stream.write
    s = style
    target_str = 'V/IS {} (VZLinux {})'.format(
        result.target_release, result.target_rhel_major)
    elapsed = _format_elapsed(result.elapsed_seconds)
    scanned_line = 'Scanned {} devices in {}.'.format(result.devices_scanned, elapsed)

    inc = result.incompatible
    dep = result.deprecated

    if not inc and not dep:
        write(s.green(s.check) + ' No compatibility issues found.\n')
        write('  ' + s.dim(scanned_line) + '\n')
        write('  ' + s.dim('Target: ' + target_str) + '\n')
        return

    if not inc:
        # Deprecated-only — not a blocker.
        n = len(dep)
        word = 'device' if n == 1 else 'devices'
        write(s.orange(s.warn) + ' Found {} deprecated {}. The upgrade can still proceed.\n'.format(n, word))
        write('\n')
        write('  ' + s.orange('Deprecated — still works, no Red Hat support') + '\n')
        for f in dep:
            write(_format_finding(f, s, verbose) + '\n')
        write('\n')
        write('  ' + scanned_line + ' ' + s.orange('{} deprecated'.format(n)) + '.\n')
        write('  ' + s.dim('Target: ' + target_str) + '\n')
        return

    # Incompatible (with or without deprecated).
    ni = len(inc)
    word = 'device' if ni == 1 else 'devices'
    write(s.red(s.cross) + ' Found {} incompatible {}. '
                          'The upgrade will not proceed until {} replaced.\n'.format(
        ni, word, 'it is' if ni == 1 else 'they are'))
    write('\n')
    write('  ' + s.red('Incompatible — must be replaced before upgrade') + '\n')
    for f in inc:
        write(_format_finding(f, s, verbose) + '\n')

    if dep:
        write('\n')
        write('  ' + s.orange('Deprecated — still works, no Red Hat support') + '\n')
        for f in dep:
            write(_format_finding(f, s, verbose) + '\n')

    write('\n')
    counts = s.red('{} incompatible'.format(ni))
    if dep:
        counts += ', ' + s.orange('{} deprecated'.format(len(dep)))
    write('  ' + scanned_line + ' ' + counts + '.\n')
    write('  ' + s.dim('Target: ' + target_str) + '\n')
