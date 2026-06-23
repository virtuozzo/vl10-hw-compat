import fnmatch
import itertools
import json
import os
import re
from enum import Enum
from pathlib import Path
from subprocess import run, PIPE, DEVNULL


DATA_DIR = Path(__file__).parent / 'data'
COMPAT_DB_PATH = DATA_DIR / 'device_driver_deprecation_data.json'
EXCEPTIONS_DB_PATH = DATA_DIR / 'device_driver_exceptions.json'
KMOD_INDEX_DIR = DATA_DIR / 'kmod-idx'


class Severity(Enum):
    incompatible = 'incompatible'  # driver removed — blocks upgrade
    deprecated = 'deprecated'      # still works, no Red Hat support


class _Status(Enum):
    ok = 0
    removed = 1
    unmaintained = 2


_STATUS_TO_SEVERITY = {
    _Status.removed: Severity.incompatible,
    _Status.unmaintained: Severity.deprecated,
}


class Finding:
    __slots__ = ('severity', 'name', 'driver', 'slot', 'modalias',
                 'sysfs_path', 'kind', 'raw_entry')

    def __init__(self, severity, name, driver=None, slot=None,
                 modalias=None, sysfs_path=None, kind='device', raw_entry=None):
        self.severity = severity
        self.name = name
        self.driver = driver
        self.slot = slot
        self.modalias = modalias
        self.sysfs_path = sysfs_path
        self.kind = kind
        self.raw_entry = raw_entry

    def to_dict(self):
        return {
            'severity': self.severity.value,
            'name': self.name,
            'driver': self.driver,
            'slot': self.slot,
            'modalias': self.modalias,
            'sysfs_path': self.sysfs_path,
            'kind': self.kind,
        }


class ScanResult:
    __slots__ = ('findings', 'devices_scanned', 'target_release',
                 'target_rhel_major', 'elapsed_seconds')

    def __init__(self, findings, devices_scanned, target_release,
                 target_rhel_major, elapsed_seconds):
        self.findings = findings
        self.devices_scanned = devices_scanned
        self.target_release = target_release
        self.target_rhel_major = target_rhel_major
        self.elapsed_seconds = elapsed_seconds

    @property
    def incompatible(self):
        return [f for f in self.findings if f.severity == Severity.incompatible]

    @property
    def deprecated(self):
        return [f for f in self.findings if f.severity == Severity.deprecated]


class NullProgress:
    """No-op progress reporter used when no UI is attached."""
    def phase(self, idx, total_phases, name, total_steps=None):
        pass

    def tick(self, n=1):
        pass

    def done(self):
        pass


class KMod:
    def __init__(self, index_dir):
        from ctypes import CDLL, c_void_p, c_char_p, c_int, byref, POINTER

        lib = CDLL('libkmod.so.2')

        ctx_t = c_void_p
        kmod_list_p = c_void_p

        kmod_new = lib.kmod_new
        kmod_new.argtypes = (c_char_p, c_void_p)
        kmod_new.restype = ctx_t

        kmod_load_resources = lib.kmod_load_resources
        kmod_load_resources.argtypes = (ctx_t,)
        kmod_load_resources.restype = c_int

        kmod_module_new_from_lookup = lib.kmod_module_new_from_lookup
        kmod_module_new_from_lookup.argtypes = (ctx_t, c_char_p, POINTER(kmod_list_p))
        kmod_module_new_from_lookup.restype = c_int

        kmod_module_unref_list = lib.kmod_module_unref_list
        kmod_module_unref_list.argtypes = (c_void_p,)
        kmod_module_unref_list.restype = c_int

        null = c_void_p()
        ctx = kmod_new(str(index_dir).encode(), byref(null))
        if not ctx:
            raise Exception('kmod_new() failed')

        if kmod_load_resources(ctx) < 0:
            raise Exception('kmod_load_resources() failed')

        self._make_mod_list = c_void_p
        self._destroy_mod_list = kmod_module_unref_list
        self._lookup = lambda name, mod_list: kmod_module_new_from_lookup(
            ctx, name.encode(), byref(mod_list))

    def has_module(self, modalias):
        mod_list = self._make_mod_list()
        if self._lookup(modalias, mod_list) < 0:
            raise Exception('Module lookup failed')
        rv = bool(mod_list)
        self._destroy_mod_list(mod_list)
        return rv


def _norm_mod(name):
    return name.replace('-', '_')


class Device:
    def __init__(self, sysfs_path, modalias, modules):
        self.sysfs_path = sysfs_path
        self.modalias = modalias
        self.modules = [_norm_mod(mod) for mod in modules]

        mod_symlink = os.path.join(self.sysfs_path, 'driver/module')
        try:
            mod_rel_path = os.readlink(mod_symlink)
        except FileNotFoundError:
            self.current_module = None
        else:
            self.current_module = os.path.basename(mod_rel_path)


class PCIDevice(Device):
    def __init__(self, attrs, pci_id):
        sysfs_path = os.path.join('/sys/bus/pci/devices', attrs['Slot'])
        with open(os.path.join(sysfs_path, 'modalias')) as f:
            modalias = f.read().rstrip()

        Device.__init__(self, sysfs_path, modalias, attrs['Module'])

        self.attrs = attrs
        self.pci_id = pci_id

    def display_name(self):
        vendor = self.attrs.get('Vendor', '') or ''
        device = self.attrs.get('Device', '') or ''
        s = ' '.join(p for p in (vendor, device) if p)
        return s or self.modalias


class MiscDevice(Device):
    def display_name(self):
        return self.modalias or self.sysfs_path


def _get_pci_devices():
    id_tags = ('Vendor', 'Device', 'SVendor', 'SDevice')
    id_re = re.compile(r'^\s*(.*) \[([0-9a-fA-F]+)\]$')

    def parse_id(x):
        if x is None:
            return '', 0
        desc, id_num = id_re.match(x).groups()
        return desc, int(id_num, 16)

    tag_re = re.compile(r'^(\w+):\t(.*)$', re.MULTILINE)
    multi_value_tags = ('Module',)

    p = run(['lspci', '-vmmknnD'], stdout=PIPE, check=True)
    rv = []
    for block in p.stdout.decode().split('\n\n')[:-1]:
        attrs = {}
        for tag in multi_value_tags:
            attrs[tag] = []
        for tag, val in tag_re.findall(block):
            if tag in multi_value_tags:
                attrs[tag].append(val)
            else:
                attrs[tag] = val

        vendor_desc, vendor_id = parse_id(attrs.get('Vendor'))
        device_desc, device_id = parse_id(attrs.get('Device'))
        sub_vendor_desc, sub_vendor_id = parse_id(attrs.get('SVendor'))
        sub_device_desc, sub_device_id = parse_id(attrs.get('SDevice'))

        attrs['Vendor'] = vendor_desc
        attrs['Device'] = device_desc
        attrs['SVendor'] = sub_vendor_desc
        attrs['SDevice'] = sub_device_desc

        rv.append(PCIDevice(
            attrs,
            (vendor_id, device_id, sub_vendor_id, sub_device_id),
        ))

    return rv


def _list_sysfs_modaliases():
    p = run(['find', '/sys/devices', '-type', 'f', '-name', 'modalias'],
            stdout=PIPE, check=True)
    return p.stdout.decode().splitlines()


def _get_misc_devices(filenames, loaded_modules, progress):
    rv = []
    cache = {}
    for filename in filenames:
        with open(filename) as f:
            modalias = f.read().rstrip()

        if modalias.startswith('pci:') or modalias.startswith('x86cpu:'):
            progress.tick()
            continue

        if modalias in cache:
            modules = cache[modalias]
        else:
            p = run(['modprobe', '--resolve-alias', modalias],
                    stdout=PIPE, stderr=DEVNULL)
            modules = set(p.stdout.decode().splitlines()) if p.returncode == 0 else set()
            cache[modalias] = modules

        dev = MiscDevice(os.path.dirname(filename), modalias, modules)
        if (dev.current_module is None and len(modules) == 1
                and modules & loaded_modules):
            dev.current_module = list(modules)[0]
        rv.append(dev)
        progress.tick()

    return rv


def _get_loaded_modules():
    p = run(['lsmod'], stdout=PIPE, check=True)
    lines = p.stdout.decode().splitlines()
    return {_norm_mod(l.split()[0]) for l in lines[1:]}


def _get_all_modules():
    return {_norm_mod(m) for m in os.listdir('/sys/module')}


def _pci_id_entry_map(compat_db):
    def parse_pci_id(pci_id):
        return tuple(int(x, 16) for x in pci_id.split(':'))

    return {
        parse_pci_id(ent['device_id']): ent
        for ent in compat_db
        if ent['device_type'] == 'pci' and ent['device_id']
    }


def _module_entry_map(compat_db):
    return {
        _norm_mod(ent['driver_name']): ent
        for ent in compat_db
        if not ent['device_id']
    }


def _classify_entry(entry, rhel_major):
    if entry is None:
        return _Status.ok
    if rhel_major not in entry['available_in_rhel']:
        return _Status.removed
    if rhel_major not in entry['maintained_in_rhel']:
        return _Status.unmaintained
    return _Status.ok


def _match_devices(pci_devs, misc_devs, pci_id_entry_map, mod_entry_map):
    dev_entries = []
    dev_modules = set()
    pending_misc = list(misc_devs)

    for dev in pci_devs:
        dev_modules.update(dev.modules)
        matched = None
        for idx in (4, 3, 2):
            ent = pci_id_entry_map.get(dev.pci_id[:idx])
            if ent is not None:
                matched = ent
                break
        if matched is not None:
            dev_entries.append((dev, None, matched))
        else:
            pending_misc.append(dev)

    for dev in pending_misc:
        dev_modules.update(dev.modules)
        dev_entries.append((
            dev,
            dev.current_module,
            mod_entry_map.get(dev.current_module) if dev.current_module else None,
        ))

    return dev_entries, dev_modules


def _device_to_finding(dev, mod, ent, status):
    severity = _STATUS_TO_SEVERITY[status]
    driver = mod or (ent.get('driver_name') if ent else None) or None

    if isinstance(dev, PCIDevice):
        name = dev.display_name()
        slot = dev.attrs.get('Slot')
        if (not name or name == dev.modalias) and ent and ent.get('device_name'):
            name = ent['device_name']
    else:
        slot = None
        name = ent.get('device_name') if ent and ent.get('device_name') else dev.display_name()

    return Finding(
        severity=severity,
        name=name,
        driver=driver,
        slot=slot,
        modalias=dev.modalias,
        sysfs_path=dev.sysfs_path,
        kind='device',
        raw_entry=ent,
    )


def _module_to_finding(mod_name, ent, status):
    severity = _STATUS_TO_SEVERITY[status]
    name = ent.get('device_name') if ent and ent.get('device_name') else 'driver ' + mod_name
    return Finding(
        severity=severity,
        name=name,
        driver=mod_name,
        kind='module',
        raw_entry=ent,
    )


def _load_compat_db():
    with open(COMPAT_DB_PATH) as f:
        return json.load(f)['data']


def _load_exc_predicate():
    with open(EXCEPTIONS_DB_PATH) as f:
        patterns = json.load(f)
    if not patterns:
        return lambda name: False
    patterns_regexp = '|'.join(map(fnmatch.translate, patterns))
    return re.compile(patterns_regexp).match


_TARGET_RELEASE_TO_RHEL = {
    '8.0': 10,
}


def resolve_target(target_release):
    """Map a V/IS release string to its upstream RHEL major version."""
    try:
        return _TARGET_RELEASE_TO_RHEL[target_release]
    except KeyError:
        valid = ', '.join(sorted(_TARGET_RELEASE_TO_RHEL))
        raise ValueError(
            'Unknown target release {!r}. Supported: {}'.format(target_release, valid))


def scan(target_release='8.0', skip_kmod=False, progress=None):
    """Run a full hardware compatibility scan.

    Returns a ScanResult. `progress` is an optional reporter with `.phase()`,
    `.tick()`, and `.done()` methods (see NullProgress).
    """
    import time

    if progress is None:
        progress = NullProgress()

    rhel_major = resolve_target(target_release)
    started = time.monotonic()

    compat_db = _load_compat_db()
    exc_pred = _load_exc_predicate()
    pci_id_map = _pci_id_entry_map(compat_db)
    mod_map = _module_entry_map(compat_db)

    progress.phase(1, 4, 'Enumerating PCI devices', None)
    pci_devs = _get_pci_devices()
    progress.done()

    progress.phase(2, 4, 'Reading sysfs', None)
    sysfs_files = _list_sysfs_modaliases()
    progress.done()

    progress.phase(3, 4, 'Resolving drivers', len(sysfs_files))
    loaded_modules = _get_loaded_modules()
    misc_devs = _get_misc_devices(sysfs_files, loaded_modules, progress)
    progress.done()

    progress.phase(4, 4, 'Classifying compatibility', None)
    dev_entries, dev_modules = _match_devices(
        pci_devs, misc_devs, pci_id_map, mod_map)

    kmod = None
    if not skip_kmod:
        try:
            kmod = KMod(KMOD_INDEX_DIR / str(rhel_major))
        except Exception:
            kmod = None

    builtin_modules = _get_all_modules() - loaded_modules

    findings = []
    for dev, mod, ent in dev_entries:
        if ent is None:
            if dev.current_module is None:
                continue
            if not dev.modules and dev.current_module not in builtin_modules:
                continue
            if kmod is None or kmod.has_module(dev.modalias):
                continue
            if exc_pred(dev.modalias):
                continue
            findings.append(Finding(
                severity=Severity.incompatible,
                name=dev.display_name() if hasattr(dev, 'display_name') else dev.modalias,
                driver=dev.current_module,
                slot=getattr(dev, 'attrs', {}).get('Slot') if isinstance(dev, PCIDevice) else None,
                modalias=dev.modalias,
                sysfs_path=dev.sysfs_path,
            ))
            continue

        st = _classify_entry(ent, rhel_major)
        if st == _Status.ok:
            continue
        name_key = mod if mod else dev.modalias
        if exc_pred(name_key):
            continue
        findings.append(_device_to_finding(dev, mod, ent, st))

    for mod in sorted(loaded_modules - dev_modules):
        ent = mod_map.get(mod)
        st = _classify_entry(ent, rhel_major)
        if st == _Status.ok:
            continue
        if exc_pred(mod):
            continue
        findings.append(_module_to_finding(mod, ent, st))

    progress.done()

    devices_scanned = len(pci_devs) + len(misc_devs)
    elapsed = time.monotonic() - started

    return ScanResult(
        findings=findings,
        devices_scanned=devices_scanned,
        target_release=target_release,
        target_rhel_major=rhel_major,
        elapsed_seconds=elapsed,
    )
