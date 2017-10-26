import os.path
import os
import logging
import hashlib
import json
import datetime
from pytz import timezone
import iso8601

log = logging.getLogger(__name__)

time_zone = timezone(os.getenv('TZ', "America/New_York"))


class Inventory:
    def __init__(self, path, dirs=None, files=None):
        self.path = path
        self.dirs = set(dirs or [])
        self.files = files or {}
        self.inventory_filepath = self._generate_inventory_filepath(self.path)
        self.timestamp = datetime_now()

    @staticmethod
    def perform_inventory(fs_path, base_fs_path):
        log.info('Inventorying %s', fs_path)
        inventory = Inventory(Inventory._remove_base_path(fs_path, base_fs_path))
        for child in os.listdir(fs_path):
            child_path = os.path.join(fs_path, child)
            if os.path.isdir(child_path):
                inventory.dirs.add(child)
            elif os.path.isfile(child_path):
                inventory.files[child] = Inventory._generate_fixity(child_path)
        return inventory

    @staticmethod
    def perform_recursive_inventory(fs_path, fs_base_path):
        inventories = [Inventory.perform_inventory(fs_path, fs_base_path)]
        for dir_name in inventories[0].dirs:
            inventories.extend(Inventory.perform_recursive_inventory(os.path.join(fs_path, dir_name), fs_base_path))
        return inventories

    @staticmethod
    def _remove_base_path(path, base_path):
        return os.path.relpath(path, base_path)

    @staticmethod
    def _generate_fixity(filepath):
        sha256 = hashlib.sha256()

        with open(filepath, 'rb') as f:
            while True:
                data = f.read(65536)
                if not data:
                    break
                sha256.update(data)

        return sha256.hexdigest()

    @staticmethod
    def _generate_inventory_filepath(path):
        digest = hashlib.sha256(path.encode('utf-8')).hexdigest()
        return os.path.join(os.path.join(*[digest[i:i + 8] for i in range(0, 64, 8)]), '{}.json'.format(digest))

    def as_dict(self):
        return {
            'path': self.path,
            'dirs': sorted(list(self.dirs)),
            'files': self.files,
            'timestamp': self.timestamp.isoformat()
        }

    def write(self, base_inventory_path):
        filepath = os.path.join(base_inventory_path, self.inventory_filepath)
        log.debug('Writing inventory for %s to %s', self.path, filepath)
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, 'w') as f:
            json.dump(self.as_dict(), f, indent=2)
        return filepath

    def diff(self, that_inventory):
        assert self.path == that_inventory.path
        directories_missing_from_this = that_inventory.dirs - self.dirs
        directories_missing_from_that = self.dirs - that_inventory.dirs
        files_missing_from_this = self._files_missing_from_that(that_inventory.files, self.files)
        files_missing_from_that = self._files_missing_from_that(self.files, that_inventory.files)
        file_fixity_mismatch = {}

        for file, fixity in self.files.items():
            if file in that_inventory.files and fixity != that_inventory.files[file]:
                file_fixity_mismatch[file] = (fixity, that_inventory.files[file])

        return (directories_missing_from_this, directories_missing_from_that,
                files_missing_from_this, files_missing_from_that, file_fixity_mismatch)

    @staticmethod
    def _files_missing_from_that(this_files, that_files):
        missing_files = {}
        for file in set(this_files.keys()) - set(that_files.keys()):
            missing_files[file] = this_files[file]
        return missing_files

    @staticmethod
    def read(path, base_inventory_path):
        inventory_filepath = os.path.join(base_inventory_path, Inventory._generate_inventory_filepath(path))
        log.debug('Reading inventory for %s from %s', path, inventory_filepath)
        with open(inventory_filepath) as f:
            inventory_json = json.load(f)
        return Inventory(inventory_json['path'], inventory_json['dirs'], inventory_json['files'])

    def update(self, directories_missing_from_this, directories_missing_from_that, files_missing_from_this,
               files_missing_from_that, file_fixity_mismatch, timestamp=None):
        log.info('Updating inventory for %s', self.path)

        # Add directories missing from this
        self.dirs = self.dirs | directories_missing_from_this

        # Remove directories missing from that
        self.dirs = self.dirs - directories_missing_from_that

        # Add files missing from this
        self.files.update(files_missing_from_this)

        # Remove files missing from that
        for file in files_missing_from_that.keys():
            del self.files[file]

        # Update file fixity mismatches
        for file, (_, fixity) in file_fixity_mismatch.items():
            assert self.files[file] != fixity
            self.files[file] = fixity

        self.timestamp = timestamp or datetime_now()


class InventoryDiff:
    def __init__(self, path, directories_missing_from_inventory, directories_missing_from_fs,
                 files_missing_from_inventory, files_missing_from_fs, file_fixity_mismatch, timestamp=None):
        self.path = path
        self.directories_missing_from_fs = directories_missing_from_fs
        self.directories_missing_from_inventory = directories_missing_from_inventory
        self.files_missing_from_fs = files_missing_from_fs
        self.files_missing_from_inventory = files_missing_from_inventory
        self.file_fixity_mismatch = file_fixity_mismatch
        self.timestamp = timestamp or datetime_now()

    def as_dict(self):
        return {
            'path': self.path,
            'directories_missing_from_fs': list(self.directories_missing_from_fs),
            'directories_missing_from_inventory': list(self.directories_missing_from_inventory),
            'files_missing_from_fs': self.files_missing_from_fs,
            'files_missing_from_inventory': self.files_missing_from_inventory,
            'file_fixity_mismatch': self.file_fixity_mismatch,
            'timestamp': self.timestamp.isoformat()
        }

    def has_diffs(self):
        return self.directories_missing_from_fs or self.directories_missing_from_inventory or \
               self.files_missing_from_fs or self.files_missing_from_inventory or self.file_fixity_mismatch

    @staticmethod
    def generate_inventory_diff(fs_inventory, inventory_inventory):
        return InventoryDiff(fs_inventory.path, *inventory_inventory.diff(fs_inventory))

    @staticmethod
    def from_dict(inventory_dict):
        return InventoryDiff(
            inventory_dict['path'],
            set(inventory_dict['directories_missing_from_inventory']),
            set(inventory_dict['directories_missing_from_fs']),
            inventory_dict['files_missing_from_inventory'],
            inventory_dict['files_missing_from_fs'],
            inventory_dict['file_fixity_mismatch'],
            parse_datetime(inventory_dict['timestamp'])
        )


class InventoryReport:
    def __init__(self, base_path, inventory_diffs=None, timestamp=None):
        self.base_path = base_path
        self.timestamp = timestamp or datetime_now()
        self.inventory_diffs = inventory_diffs or []
        timestamp = self.timestamp.isoformat()
        self.report_filepath = os.path.join(timestamp[0:4], timestamp[5:7], timestamp[8:10], '{}.json'.format(timestamp))

    def as_dict(self):
        report_dict = {
            'base_path': self.base_path,
            'timestamp': self.timestamp.isoformat(),
            'inventory_diffs': []
        }
        for inventory_diff in self.inventory_diffs:
            report_dict['inventory_diffs'].append(inventory_diff.as_dict())
        return report_dict

    def write(self, base_report_path):
        filepath = os.path.join(base_report_path, self.report_filepath)
        log.debug('Writing report for %s to %s', self.base_path, filepath)
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, 'w') as f:
            json.dump(self.as_dict(), f, indent=2)
        return filepath

    @staticmethod
    def read(report_filepath):
        with open(report_filepath) as f:
            report_json = json.load(f)

        inventory_report = InventoryReport(report_json['base_path'], timestamp=parse_datetime(report_json['timestamp']))
        for inventory_diff_dict in report_json['inventory_diffs']:
            inventory_report.inventory_diffs.append(InventoryDiff.from_dict(inventory_diff_dict))
        return inventory_report


def datetime_now():
    return datetime.datetime.now(time_zone)


def parse_datetime(datetime_str):
    return iso8601.parse_date(datetime_str)