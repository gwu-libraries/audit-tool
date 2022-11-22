config = {
        # File system base paths, inventory base paths, reports base paths.
        # A file system base path is the root of a section of the file system that
        # will be inventoried.
        # Each file system base path will have its own inventory and reports.
        # The inventory filepath contains the inventory records for the file system.
        # The reports filepath contains the reports for the file system.
        'file_systems': [('/Users/justinlittman/tmp/inventory/fs',
                          '/Users/justinlittman/tmp/inventory/inventory',
                          '/Users/justinlittman/tmp/inventory/reports')],
        # Location of report index database.
        'report_index_db': '/Users/justinlittman/tmp/inventory/reports.db',
        # Email configuration - if gwu account, use @email.gwu.edu, not @gwu.edu
        'email': {
            'username': 'someone@email.gwu.edu',
            'password': 'password',
            'port': 587,
            'host': 'smtp.gwu.edu',
            'send_to': ['someone@gwu.edu', 'someone_else@gwu.edu']
        },
        # Number of threads to use for fixity checking.
        'fixity_threads': 3
}
