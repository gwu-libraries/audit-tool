from inventory import Inventory, InventoryDiff, InventoryReport
import logging
import argparse
import sys
import shutil
import os
import json
import sqlite3
from threading import get_ident
from collections import namedtuple
import smtplib
import getpass
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import COMMASPACE, formatdate

log = logging.getLogger(__name__)


class InventoryManager:
    def __init__(self, base_fs_path, base_inventory_path, fixity_threads=1):
        self.base_fs_path = base_fs_path
        self.base_inventory_path = base_inventory_path
        self.fixity_threads = fixity_threads

    def detect_change(self, path):
        log.info('Detecting changes for %s', path)
        inventory_diffs = []
        fs_inventories = Inventory.perform_recursive_inventory(path, self.base_fs_path,
                                                               fixity_threads=self.fixity_threads)
        # If path is not base path, check parent non-recursively to make sure added/deleted directory handled.
        if path != self.base_fs_path:
            fs_inventories.append(
                Inventory.perform_inventory(os.path.dirname(path), self.base_fs_path,
                                            fixity_threads=self.fixity_threads))
        for fs_inventory in fs_inventories:
            try:
                inventory_inventory = Inventory.read(fs_inventory.path, self.base_inventory_path)
            except FileNotFoundError:
                inventory_inventory = Inventory(fs_inventory.path)
            inventory_diff = InventoryDiff.generate_inventory_diff(fs_inventory, inventory_inventory)
            if inventory_diff.has_diffs():
                inventory_diffs.append(inventory_diff)
        return InventoryReport(path, inventory_diffs)

    def update_inventory(self, inventory_report):
        for inventory_diff in inventory_report.inventory_diffs:
            log.info('Updating inventory for %s', inventory_diff.path)
            try:
                inventory_inventory = Inventory.read(inventory_diff.path, self.base_inventory_path)
            except FileNotFoundError:
                inventory_inventory = Inventory(inventory_diff.path)
            inventory_inventory.update(inventory_diff.directories_missing_from_inventory,
                                       inventory_diff.directories_missing_from_fs,
                                       inventory_diff.files_missing_from_inventory,
                                       inventory_diff.files_missing_from_fs,
                                       inventory_diff.file_fixity_mismatch,
                                       timestamp=inventory_diff.timestamp)
            inventory_inventory.write(self.base_inventory_path)


InventoryReportSummary = namedtuple('InventoryReportSummary',
                                    ['report_filepath', 'report_timestamp', 'report_applied_timestamp', 'base_path',
                                     'has_diffs'])


class InventoryReportsIndex:
    def __init__(self, db_filepath):
        self._connection_cache = {}
        self.db_filepath = db_filepath
        # Create db if it doesn't exist
        self._create_db()

    def _get_conn(self):
        thread_id = get_ident()
        if thread_id not in self._connection_cache:
            self._connection_cache[thread_id] = sqlite3.connect(self.db_filepath,
                                                                detect_types=(sqlite3.PARSE_DECLTYPES
                                                                              | sqlite3.PARSE_COLNAMES))

        return self._connection_cache[thread_id]

    def _create_db(self):
        conn = self._get_conn()
        with conn:
            conn.execute(
                'create table if not exists reports (report_filepath primary key, report_timestamp timestamp, '
                'report_applied_timestamp timestamp, base_path, has_diffs boolean);')

    def add_report(self, inventory_report, report_filepath):
        conn = self._get_conn()
        with conn:
            conn.execute('insert into reports (report_filepath, report_timestamp, base_path, has_diffs) values '
                         '(?, ?, ?, ?);', (report_filepath, inventory_report.timestamp,
                                           inventory_report.base_path, bool(inventory_report.inventory_diffs)))

    def update_applied_timestamp(self, inventory_report, report_filepath):
        conn = self._get_conn()
        with conn:
            conn.execute('update reports set report_applied_timestamp=? where report_filepath=?',
                         (inventory_report.applied_timestamp, report_filepath))

    def get_reports(self, limit=25, has_diffs_only=False):
        sql = 'select report_filepath, report_timestamp, report_applied_timestamp, base_path, has_diffs from reports'
        params = []
        if has_diffs_only:
            sql += ' where has_diffs=?'
            params.append(True)
        sql += ' order by report_timestamp desc'
        return list(map(InventoryReportSummary._make, self._get_conn().execute(sql, params).fetchmany(limit)))


def find_base_path(base_paths, path):
    for base_path in base_paths:
        if path.startswith(base_path):
            return base_path
    raise Exception('{} is not contained in available base paths: {}.'.format(path, base_paths))

def check_json_report(path):
    ''' check to see if it's a json report to convert'''
    if path.endswith('.json'):
        pass
    else:
        raise Exception('{} is not a json report. Provide a full path to a json report.'.format(path))

def send_notification(send_to, subject, text, host, port, username, password, filepath=None, excel=None):
    msg = MIMEMultipart()
    msg['From'] = username
    msg['To'] = COMMASPACE.join(send_to)
    msg['Date'] = formatdate(localtime=True)
    msg['Subject'] = subject

    msg.attach(MIMEText(text))

    if filepath:
        with open(filepath, "rb") as f:
            part = MIMEApplication(f.read(), Name=os.path.basename(filepath))
        part['Content-Disposition'] = 'attachment; filename="%s"' % os.path.basename(filepath)
        msg.attach(part)

        if excel:
            with open(excel, "rb") as f:
                part = MIMEApplication(f.read(), Name=os.path.basename(excel))
            part['Content-Disposition'] = 'attachment; filename="%s"' % os.path.basename(excel)
            msg.attach(part)

    smtpserver = smtplib.SMTP(host, port)
    smtpserver.ehlo()
    smtpserver.starttls()
    smtpserver.ehlo
    smtpserver.login(username, password)
    smtpserver.send_message(msg, from_addr=username, to_addrs=send_to)
    log.debug('Sent email (%s) to %s', subject, ', '.join(send_to))


if __name__ == '__main__':
    from config import config

    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest='command', help='command help')

    populate_parser = subparsers.add_parser('populate', help='Populate an inventory for a file system base path, '
                                                             'deleting an existing inventory, and not requiring '
                                                             'approval of inventory changes. Use carefully!')
    populate_parser.add_argument('--exists-ok', action='store_true', help='Allow populate if inventory already exists.')
    inventory_map = {}
    report_map = {}
    report_reverse_map = {}
    for file_system_base_path, inventory_base_path, report_base_path in config['file_systems']:
        inventory_map[file_system_base_path] = inventory_base_path
        report_map[file_system_base_path] = report_base_path
        report_reverse_map[report_base_path] = file_system_base_path
    populate_parser.add_argument('file_system_base_path', choices=inventory_map.keys(),
                                 help='Choices are: {}'.format(', '.join(inventory_map.keys())))

    detect_parser = subparsers.add_parser('detect_changes', help='Compare files/directories against the inventory')
    detect_parser.add_argument('path', help='Base path to compare.')
    detect_parser.add_argument('--no-report', action='store_true', help='Don\'t write the report.')
    detect_parser.add_argument('--excel', action='store_true', help='If writing a report, also write an Excel report.')
    notify_choices = ('all', 'error_only')
    detect_parser.add_argument('--notify', choices=notify_choices,
                               help='Send email notification. Choices are: '.format(', '.join(notify_choices)))

    update_parser = subparsers.add_parser('update', help='Update inventory')
    update_parser.add_argument('report_path', help='Filepath of inventory report to use for update.')

    list_parser = subparsers.add_parser('list_reports', help='List inventory reports.')
    list_parser.add_argument('--limit', type=int, default=10, help='Number of reports to return.')
    list_parser.add_argument('--has-diffs-only', action='store_true', help='Limit to report with diffs only.')

    excel_parser = subparsers.add_parser('excel', help='Write report to Excel')
    excel_parser.add_argument('report_path', help='Filepath of inventory report to write to Excel.')

    note_parser = subparsers.add_parser('note', help='Add a note to a report')
    user = getpass.getuser()
    note_parser.add_argument('--user', help='Author of note. Default is {}.'.format(user), default=user)
    note_parser.add_argument('report_path', help='Filepath of inventory report to which to add note.')
    note_parser.add_argument('text', help='Note text. Make sure to quote.')

    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG)

    inventory_report_index = InventoryReportsIndex(config['report_index_db'])

    if args.command is None:
        parser.print_help()
        sys.exit(1)
    elif args.command == 'populate':
        file_system_base_path = args.file_system_base_path
        inventory_base_path = inventory_map[file_system_base_path]
        if not args.exists_ok and os.path.exists(inventory_base_path):
            print('Inventory already exists for {} so refusing to populate. Use --exists-ok to override.'.format(
                file_system_base_path))
            sys.exit(1)
        shutil.rmtree(inventory_base_path, ignore_errors=True)
        os.makedirs(inventory_base_path)
        inventory_manager = InventoryManager(file_system_base_path, inventory_base_path,
                                             fixity_threads=config['fixity_threads'])
        inventory_diffs = inventory_manager.detect_change(file_system_base_path)
        inventory_manager.update_inventory(inventory_diffs)
        print('Populated inventory from {}'.format(file_system_base_path))
    elif args.command == 'detect_changes':
        file_system_base_path = find_base_path(inventory_map.keys(), args.path)
        inventory_manager = InventoryManager(file_system_base_path, inventory_map[file_system_base_path],
                                             fixity_threads=config['fixity_threads'])
        inventory_report = inventory_manager.detect_change(args.path)
        if args.no_report:
            print(json.dumps(inventory_report.as_dict(), indent=2))
        else:
            report_filepath = inventory_report.write(report_map[file_system_base_path])
            inventory_report_index.add_report(inventory_report, report_filepath)
            excel_report_filepath = None
            if args.excel:
                excel_report_filepath = inventory_report.write_excel(report_map[file_system_base_path])
                print('Wrote Excel report to {}'.format(excel_report_filepath))
            if args.notify == 'all' or (args.notify == 'error_only' and inventory_report.inventory_diffs):
                send_notification(config['email']['send_to'],
                                  '{}hanges detected in {}'.format(
                                      'C' if inventory_report.inventory_diffs else 'No c',
                                      args.path),
                                  'Report{} attached and at:\n{}{}.'.format('s are' if
                                      args.excel else ' is', report_filepath,
                                      '\nand \n' + excel_report_filepath if
                                      args.excel else ''),
                                  config['email']['host'],
                                  config['email']['port'],
                                  config['email']['username'],
                                  config['email']['password'],
                                  filepath=report_filepath, excel=excel_report_filepath)
            print('Wrote report to {}'.format(report_filepath))
    elif args.command == 'update':
        report_base_path = find_base_path(report_reverse_map.keys(), args.report_path)
        file_system_base_path = report_reverse_map[report_base_path]
        inventory_report = InventoryReport.read(args.report_path)
        if inventory_report.notes:
            print("Here are the notes:")
            for note in inventory_report.notes:
                print('{} ({}): {}'.format(note.user, note.timestamp.isoformat(), note.text))
            if input('Proceed? [Yn]: ').lower() == 'n':
                sys.exit()
        inventory_manager = InventoryManager(file_system_base_path, inventory_map[file_system_base_path],
                                             fixity_threads=config['fixity_threads'])
        inventory_manager.update_inventory(inventory_report)
        inventory_report.applied()
        inventory_report.write(report_base_path)
        inventory_report_index.update_applied_timestamp(inventory_report, args.report_path)
        print('Updated inventory from {}'.format(args.report_path))
    elif args.command == 'excel':
        check_json_report(args.report_path)
        report_base_path = find_base_path(report_reverse_map.keys(), args.report_path)
        file_system_base_path = report_reverse_map[report_base_path]
        inventory_report = InventoryReport.read(args.report_path)
        excel_filepath = inventory_report.write_excel(report_base_path)
        print('Wrote excel report to {}'.format(excel_filepath))
    elif args.command == 'note':
        report_base_path = find_base_path(report_reverse_map.keys(), args.report_path)
        file_system_base_path = report_reverse_map[report_base_path]
        inventory_report = InventoryReport.read(args.report_path)
        inventory_report.add_note(args.text, args.user)
        inventory_report.write(report_base_path)
        print('Added note to {}'.format(args.report_path))
    elif args.command == 'list_reports':
        for report_summary in inventory_report_index.get_reports(limit=args.limit,
                                                                 has_diffs_only=args.has_diffs_only):
            print('{} (Created on {}. Base path is {}.{}{})'
                  .format(report_summary.report_filepath,
                          report_summary.report_timestamp,
                          report_summary.base_path,
                          ' Applied on {}.'.format(
                              report_summary.report_applied_timestamp) if report_summary.report_applied_timestamp
                          else '',
                          ' Has diffs.' if report_summary.has_diffs else ''))
