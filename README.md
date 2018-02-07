# inventory
Simple inventory and auditing for files/directories stored on file system.

## Install
1. Clone this repo.

        git clone https://github.com/gwu-libraries/inventory.git

2. Copy `example.config.py` to `config.py`.

        cd inventory
        cp example.config.py config.py
        
3. Edit the values in `config.py`. This file contains explanations of the values.
4. Optional: Create a virtualenv.

        virtualenv -p python3 ENV
        source ENV/bin/activate

5. Install requirements.

        pip install -r requirements.txt
        
6. Optional: Schedule audits with cron.

        0 12 * * Sat /opt/inventory/ENV/bin/python /opt/inventory/inventory_manager.py detect_changes --notify all /storage/Drobo5Volume1

Note: If multiple users will be performing inventorying activities, be cognizant of file permissions. One useful approach is to have all users in a common group, set the python executable to that group, and set the guid & group's executable bit (`chmod g+s`) on the python executable.
        
## Initial population of inventory
When adding a new file system to the inventory, the inventory for that file system must be populated.

        python inventory_manager.py <file system base path>
        
## Updating files
1. Add, update, or delete files. If copying files from other storage, that copy should be verified (e.g., by using rsync or checking fixities before and after copy).
2. Detect the added, updated, or deleted files.

        python inventory_manager.py detect_changes <base path containing the changes>
        
3. Detecting the changes will produce an inventory report, describing all of the changes that were detected. Review the report to make sure that it is accurate. To keep track of your progress, you may want to add notes.
4. Update the inventory.

        python inventory_manager.py update <path to report>
        
Note: If an report has notes, you will be asked to confirm before proceeding.
        
## Auditing files
1. Detect changes either by a scheduled cron job or by manually invoking:

        python inventory_manager.py detect_changes <file system base path>
        
2. Review the report to determine if any discrepancies were detected.
3. If a discrepancy was detected and the discrepancy was caused by a failure to update the inventory after updating files then update the inventory:

        python inventory_manager.py update <path to report>
        
4. If a discrepancy was detected and the discrepancy was caused by an error with the files, then fix the error and re-run detect changes to make sure the problem was resolved.

## Additional functions
### Adding notes
You can add notes with:

        python inventory_manager.py note <path to report> "Your note."
        
Notes will be added to the report.

### Excel version of report
To get an Excel version of a report:

        python inventory_manager.py excel <path to report>
