import argparse
import json
import sys

from . import __version__, engine, ui


EXIT_CLEAN = 0
EXIT_DEPRECATED = 1
EXIT_INCOMPATIBLE = 2


def _build_parser():
    p = argparse.ArgumentParser(
        prog='hw-compat-check',
        description='Scan this node for hardware that may be unsupported on V/IS 8.0.',
    )
    p.add_argument('--target-release', default='8.0',
                   help=argparse.SUPPRESS)
    p.add_argument('--json', action='store_true',
                   help='Emit machine-readable JSON on stdout.')
    p.add_argument('-v', '--verbose', action='store_true',
                   help='Include modalias and sysfs path for each finding.')
    p.add_argument('--no-progress', action='store_true',
                   help='Disable progress animation.')
    p.add_argument('--no-color', action='store_true',
                   help='Disable colored output.')
    p.add_argument('--skip-kmod', action='store_true',
                   help='Do not consult kmod indexes when checking for missing drivers.')
    p.add_argument('--version', action='version',
                   version='hw-compat-check ' + __version__)
    return p


def _exit_code(result):
    if result.incompatible:
        return EXIT_INCOMPATIBLE
    if result.deprecated:
        return EXIT_DEPRECATED
    return EXIT_CLEAN


def _emit_json(result):
    payload = {
        'target_release': result.target_release,
        'target_rhel_major': result.target_rhel_major,
        'devices_scanned': result.devices_scanned,
        'elapsed_seconds': round(result.elapsed_seconds, 3),
        'findings': [f.to_dict() for f in result.findings],
        'summary': {
            'incompatible': len(result.incompatible),
            'deprecated': len(result.deprecated),
        },
    }
    json.dump(payload, sys.stdout, indent=2)
    sys.stdout.write('\n')


def main(argv=None):
    args = _build_parser().parse_args(argv)

    if args.json:
        progress = engine.NullProgress()
    else:
        progress = ui.make_progress(enabled=None if not args.no_progress else False)

    try:
        result = engine.scan(
            target_release=args.target_release,
            skip_kmod=args.skip_kmod,
            progress=progress,
        )
    except ValueError as exc:
        sys.stderr.write('error: ' + str(exc) + '\n')
        return 2
    except FileNotFoundError as exc:
        # lspci, modprobe, find missing — surface plainly.
        sys.stderr.write('error: required tool not found: ' + str(exc) + '\n')
        return 2

    if args.json:
        _emit_json(result)
    else:
        style = ui.make_style(color=None if not args.no_color else False)
        # Blank line between progress trace and summary.
        sys.stderr.write('\n')
        sys.stderr.flush()
        ui.render_summary(result, style=style, verbose=args.verbose)

    return _exit_code(result)


if __name__ == '__main__':
    sys.exit(main())
