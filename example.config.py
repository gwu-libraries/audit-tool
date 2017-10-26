config = {
        # File system base paths, inventory base paths, reports base paths
        'file_systems': [('/Users/justinlittman/tmp/inventory/fs',
                          '/Users/justinlittman/tmp/inventory/inventory',
                          '/Users/justinlittman/tmp/inventory/reports')],
        # Location of report index database.
        'report_index_db': '/Users/justinlittman/tmp/inventory/reports.db',
        # Email configuration
        'email': {
            'username': 'someone@email.gwu.edu',
            'password': 'password',
            'port': 587,
            'host': 'smtp.gmail.com',
            'send_to': ['someone@gwu.edu', 'someone_else@gwu.edu']
        }}
