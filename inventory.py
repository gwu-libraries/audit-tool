import os.path
import os
import logging
import hashlib
import json
import datetime
from pytz import timezone
import iso8601
import threading
import queue
import xlsxwriter
import sys

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
    def perform_inventory(fs_path, base_fs_path, fixity_threads=1):
        log.info('Inventorying %s', fs_path)
        inventory = Inventory(Inventory._remove_base_path(fs_path, base_fs_path))
        child_files = []
        for child in os.listdir(fs_path):
            child_path = os.path.join(fs_path, child)
            if os.path.isdir(child_path):
                inventory.dirs.add(child)
            elif os.path.isfile(child_path):
                # inventory.files[child] = Inventory._generate_fixity(child_path)
                child_files.append((child, child_path))
        if child_files:
            q = queue.Queue()
            threads = []
            thread_count = min(len(child_files), fixity_threads)
            for i in range(thread_count):
                t = FixityThread(q, inventory.files)
                t.start()
                threads.append(t)

            for file in child_files:
                q.put(file)

            # block until all tasks are done
            q.join()

            # stop workers
            for i in range(thread_count):
                q.put((None, None))
            for t in threads:
                t.join()
        return inventory

    @staticmethod
    def perform_recursive_inventory(fs_path, fs_base_path, fixity_threads=1):
        inventories = [Inventory.perform_inventory(fs_path, fs_base_path, fixity_threads=fixity_threads)]
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


class FixityThread(threading.Thread):
    def __init__(self, queue, files, *args, **kwargs):
        self.exc = None
        self.queue = queue
        self.files = files
        super(FixityThread, self).__init__(*args, **kwargs)

    def run(self):
        while True:
            filename, filepath = self.queue.get()
            if filepath is None:
                break
            try:
                sha256 = hashlib.sha256()
                with open(filepath, 'rb') as f:
                    while True:
                        data = f.read(65536)
                        if not data:
                            break
                        sha256.update(data)

                self.files[filename] = sha256.hexdigest()
            except:
                # Save details of the exception thrown but don't rethrow,
                import sys
                self.exc = sys.exc_info()
            self.queue.task_done()

    def join(self):
        threading.Thread.join(self)
        if self.exc:
            msg = "Thread '%s' threw an exception: %s" % (self.getName(), self.exc[1])
            new_exc = Exception(msg)
            raise new_exc.with_traceback(self.exc[2])


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


class InventoryNote:
    def __init__(self, text, user, timestamp=None):
        self.text = text
        self.user = user
        self.timestamp = timestamp or datetime_now()

    def as_dict(self):
        return {
            'text': self.text,
            'user': self.user,
            'timestamp': self.timestamp.isoformat()
        }

    @staticmethod
    def from_dict(note_dict):
        return InventoryNote(
            note_dict['text'],
            note_dict['user'],
            parse_datetime(note_dict['timestamp'])
        )


class InventoryReport:
    def __init__(self, base_path, inventory_diffs=None, timestamp=None, applied_timestamp=None, notes=None):
        self.base_path = base_path
        self.timestamp = timestamp or datetime_now()
        self.inventory_diffs = inventory_diffs or []
        timestamp = self.timestamp.isoformat()
        self.report_time_dir = os.path.join(timestamp[0:4], timestamp[5:7], timestamp[8:10])
        self.report_filename = timestamp.replace(".", "-").replace(":", "-")
        self.report_filepath = os.path.join(self.report_time_dir, '{}.json'.format(self.report_filename))
        self.applied_timestamp = applied_timestamp
        self.notes = notes or []

    def applied(self):
        self.applied_timestamp = datetime_now()

    def add_note(self, text, user, timestamp=None):
        self.notes.append(InventoryNote(text, user, timestamp=timestamp))

    def as_dict(self):
        report_dict = {
            'base_path': self.base_path,
            'timestamp': self.timestamp.isoformat(),
            'applied_timestamp': self.applied_timestamp.isoformat() if self.applied_timestamp else None,
            'inventory_diffs': [],
            'notes': []
        }
        for inventory_diff in self.inventory_diffs:
            report_dict['inventory_diffs'].append(inventory_diff.as_dict())
        for note in self.notes:
            report_dict['notes'].append(note.as_dict())
        return report_dict

    def write(self, base_report_path):
        filepath = os.path.join(base_report_path, self.report_filepath)
        log.debug('Writing JSON report for %s to %s', self.base_path, filepath)
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, 'w') as f:
            json.dump(self.as_dict(), f, indent=2)
        return filepath

    def write_excel(self, report_path):
        filepath = os.path.join(report_path, self.report_time_dir, 'inventory_report_{}.xlsx'.format(self.report_filename))
        log.debug('Writing Excel report for %s to %s', self.base_path, filepath)
        wb = xlsxwriter.Workbook(filepath)
        bold = wb.add_format({'bold': True})

        report_ws = wb.add_worksheet('Report')

        directories_missing_from_fs_ws = wb.add_worksheet('Dirs missing from fs')
        directories_missing_from_fs_ws.write(0, 0, 'Path', bold)
        directories_missing_from_fs_ws.write(0, 1, 'Directory', bold)
        directories_missing_from_fs_row = 1

        directories_missing_from_inventory_ws = wb.add_worksheet('Dirs missing from inventory')
        directories_missing_from_inventory_ws.write(0, 0, 'Path', bold)
        directories_missing_from_inventory_ws.write(0, 1, 'Directory', bold)
        directories_missing_from_inventory_row = 1

        files_missing_from_fs_ws = wb.add_worksheet('Files missing from fs')
        files_missing_from_fs_ws.write(0, 0, 'Path', bold)
        files_missing_from_fs_ws.write(0, 1, 'Directory', bold)
        files_missing_from_fs_ws.write(0, 2, 'Fixity', bold)
        files_missing_from_fs_row = 1

        files_missing_from_inventory_ws = wb.add_worksheet('Files missing from inventory')
        files_missing_from_inventory_ws.write(0, 0, 'Path', bold)
        files_missing_from_inventory_ws.write(0, 1, 'Directory', bold)
        files_missing_from_inventory_ws.write(0, 2, 'Fixity', bold)
        files_missing_from_inventory_row = 1

        file_fixity_mismatch_ws = wb.add_worksheet('File fixity mismatch')
        file_fixity_mismatch_ws.write(0, 0, 'Path', bold)
        file_fixity_mismatch_ws.write(0, 1, 'Directory', bold)
        file_fixity_mismatch_ws.write(0, 2, 'Expected fixity', bold)
        file_fixity_mismatch_ws.write(0, 3, 'Actual fixity', bold)
        file_fixity_mismatch_row = 1
        for inventory_diff in self.inventory_diffs:
            for directory in inventory_diff.directories_missing_from_fs:
                directories_missing_from_fs_ws.write(directories_missing_from_fs_row, 0, inventory_diff.path)
                directories_missing_from_fs_ws.write(directories_missing_from_fs_row, 1, directory)
                directories_missing_from_fs_row += 1
            for directory in inventory_diff.directories_missing_from_inventory:
                directories_missing_from_inventory_ws.write(directories_missing_from_inventory_row, 0,
                                                            inventory_diff.path)
                directories_missing_from_inventory_ws.write(directories_missing_from_inventory_row, 1, directory)
                directories_missing_from_inventory_row += 1
            for file, fixity in inventory_diff.files_missing_from_fs.items():
                files_missing_from_fs_ws.write(files_missing_from_fs_row, 0, inventory_diff.path)
                files_missing_from_fs_ws.write(files_missing_from_fs_row, 1, file)
                files_missing_from_fs_ws.write(files_missing_from_fs_row, 2, fixity)
                files_missing_from_fs_row += 1
            for file, fixity in inventory_diff.files_missing_from_inventory.items():
                files_missing_from_inventory_ws.write(files_missing_from_inventory_row, 0, inventory_diff.path)
                files_missing_from_inventory_ws.write(files_missing_from_inventory_row, 1, file)
                files_missing_from_inventory_ws.write(files_missing_from_inventory_row, 2, fixity)
                files_missing_from_inventory_row += 1
            for file, fixities in inventory_diff.file_fixity_mismatch.items():
                file_fixity_mismatch_ws.write(file_fixity_mismatch_row, 0, inventory_diff.path)
                file_fixity_mismatch_ws.write(file_fixity_mismatch_row, 1, file)
                file_fixity_mismatch_ws.write(file_fixity_mismatch_row, 2, fixities[0])
                file_fixity_mismatch_ws.write(file_fixity_mismatch_row, 3, fixities[1])
                file_fixity_mismatch_row += 1

        report_ws.write(0, 0, 'Content base path:', bold)
        report_ws.write(0, 1, self.base_path) 
        report_ws.write(1, 0, 'Report filepath:', bold)
        report_ws.write(1, 1, self.report_filepath)
        report_ws.write(2, 0, 'Timestamp:', bold)
        report_ws.write(2, 1, self.timestamp.isoformat())
        report_ws.write(3, 0, 'Applied timestamp:', bold)
        if self.applied_timestamp:
            report_ws.write(3, 1, self.applied_timestamp.isoformat())
        report_ws.write(4, 0, 'Dirs missing from fs:', bold)
        report_ws.write(4, 1, directories_missing_from_fs_row-1)
        report_ws.write(4, 0, 'Dirs missing from inventory:', bold)
        report_ws.write(4, 1, directories_missing_from_inventory_row-1)
        report_ws.write(5, 0, 'Files missing from fs:', bold)
        report_ws.write(5, 1, files_missing_from_fs_row-1)
        report_ws.write(6, 0, 'Files missing from inventory:', bold)
        report_ws.write(6, 1, files_missing_from_inventory_row-1)
        report_ws.write(7, 0, 'Fixity mismatches:', bold)
        report_ws.write(7, 1, file_fixity_mismatch_row-1)
        if self.notes:
            report_ws.write(9, 0, 'Note text', bold)
            report_ws.write(9, 1, 'User', bold)
            report_ws.write(9, 2, 'Timestamp', bold)
            report_row = 10 
            for note in self.notes:
                report_ws.write(report_row, 0, note.text)
                report_ws.write(report_row, 1, note.user)
                report_ws.write(report_row, 2, note.timestamp.isoformat())
                report_row += 1

        wb.close()
        return filepath

    @staticmethod
    def read(report_filepath):
        with open(report_filepath) as f:
            report_json = json.load(f)

        inventory_report = InventoryReport(report_json['base_path'],
                                           timestamp=parse_datetime(report_json['timestamp']),
                                           applied_timestamp=parse_datetime(report_json['applied_timestamp'])
                                           if report_json['applied_timestamp'] else None)
        for inventory_diff_dict in report_json['inventory_diffs']:
            inventory_report.inventory_diffs.append(InventoryDiff.from_dict(inventory_diff_dict))
        for note_dict in report_json['notes']:
            inventory_report.notes.append(InventoryNote.from_dict(note_dict))
        return inventory_report


def datetime_now():
    return datetime.datetime.now(time_zone)


def parse_datetime(datetime_str):
    return iso8601.parse_date(datetime_str)

if __name__ == '__main__':
    print('You want to run inventory_manager.py, not inventory.py.')
    sys.exit(1)
