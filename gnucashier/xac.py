from datetime import datetime

from gnucash import Session, SessionOpenMode, Transaction, Split, Account, GncCommodity


class GnuCashImporter:
    def __init__(self, base_file, export_file):
        self.base_file = base_file
        self.export_file = export_file
        self.base_session = None
        self.export_session = None
        self.base_book = None
        self.export_book = None
        
        # Maps for account lookups
        self.base_accounts = {}  # guid -> Account
        self.base_accounts_by_path = {}  # full_path -> Account
        self.export_accounts = {}  # guid -> Account
        self.export_accounts_by_guid = {}  # guid -> dict with account info
        
        # Commodities
        self.base_commodities = {}  # (namespace, mnemonic) -> Commodity
        self.export_commodities = {}
        
        # Tracking what needs to be created
        self.missing_accounts = []
        self.missing_commodities = []
        self.transactions_to_import = []
        
    def open_sessions(self):
        """Open both GnuCash sessions."""
        print("Opening GnuCash files...")
        
        self.base_session = Session(self.base_file, SessionOpenMode.SESSION_NORMAL_OPEN)
        self.base_book = self.base_session.book
        
        # Export is always read-only
        self.export_session = Session(self.export_file, SessionOpenMode.SESSION_READ_ONLY)
        self.export_book = self.export_session.book
        print("✓ Files opened successfully\n")
    
    def close_sessions(self):
        """Close both sessions."""
        if self.export_session:
            self.export_session.end()
            self.export_session.destroy()
        if self.base_session:
            self.base_session.end()
            self.base_session.destroy()
    
    def get_account_path(self, account):
        """Get the full path of an account (e.g., 'Assets:Bank:Checking')."""
        parts = []
        current = account
        while current:
            name = current.GetName()
            if name:  # Skip root account
                parts.insert(0, name)
            current = current.get_parent()
        return ':'.join(parts)
    
    def index_accounts(self):
        """Index all accounts in both books."""
        print("Indexing accounts...")
        
        # Index base book accounts
        root = self.base_book.get_root_account()
        self._index_account_recursive(root, self.base_accounts, self.base_accounts_by_path)
        
        # Index export book accounts  
        export_root = self.export_book.get_root_account()
        self._index_account_recursive(export_root, self.export_accounts, None)
        
        print(f"  Base book: {len(self.base_accounts)} accounts")
        print(f"  Export book: {len(self.export_accounts)} accounts\n")
    
    def _index_account_recursive(self, account, guid_map, path_map):
        """Recursively index accounts."""
        guid = account.GetGUID().to_string()
        guid_map[guid] = account
        
        if path_map is not None:
            path = self.get_account_path(account)
            if path:  # Skip empty path (root)
                path_map[path] = account
        
        # Recurse to children
        for child in account.get_children():
            self._index_account_recursive(child, guid_map, path_map)
    
    def index_commodities(self):
        """Index all commodities in both books."""
        print("Indexing commodities...")
        
        # Base book commodities
        commodity_table = self.base_book.get_table()
        for namespace_obj in commodity_table.get_namespaces_list():
            namespace = namespace_obj.get_name()
            for commodity in namespace_obj.get_commodity_list():
                key = (namespace, commodity.get_mnemonic())
                self.base_commodities[key] = commodity
        
        # Export book commodities
        export_table = self.export_book.get_table()
        for namespace_obj in export_table.get_namespaces_list():
            namespace = namespace_obj.get_name()
            for commodity in namespace_obj.get_commodity_list():
                key = (namespace, commodity.get_mnemonic())
                self.export_commodities[key] = commodity
        
        print(f"  Base book: {len(self.base_commodities)} commodities")
        print(f"  Export book: {len(self.export_commodities)} commodities\n")
    
    def analyze_import(self):
        """Analyze what needs to be imported (dry run)."""
        print("=" * 70)
        print("DRY RUN - Analyzing import requirements")
        print("=" * 70)
        print()
        
        # Check commodities
        self._check_missing_commodities()
        
        # Check accounts
        self._check_missing_accounts()
        
        # Count transactions
        self._analyze_transactions()
        
        # Summary
        self._print_summary()
    
    def _check_missing_commodities(self):
        """Find commodities that need to be created."""
        for key, commodity in self.export_commodities.items():
            if key not in self.base_commodities:
                self.missing_commodities.append(commodity)
        
        if self.missing_commodities:
            print(f"Missing Commodities ({len(self.missing_commodities)}):")
            for comm in self.missing_commodities:
                print(f"  • {comm.get_namespace()}:{comm.get_mnemonic()} - {comm.get_fullname()}")
            print()
    
    def _check_missing_accounts(self):
        """Find accounts that need to be created."""
        checked = set()
        
        # Check all accounts in export book
        for guid, account in self.export_accounts.items():
            self._check_account_hierarchy(account, checked)
        
        if self.missing_accounts:
            print(f"Missing Accounts ({len(self.missing_accounts)}):")
            for acc_info in self.missing_accounts:
                path = acc_info['path']
                acc_type = acc_info['type']
                commodity = acc_info['commodity']
                print(f"  • {path} ({acc_type}) [{commodity}]")
            print()
    
    def _check_account_hierarchy(self, account, checked):
        """Recursively check if account and its parents exist in base book."""
        guid = account.GetGUID().to_string()
        
        if guid in checked:
            return
        checked.add(guid)
        
        # Check parent first
        parent = account.get_parent()
        if parent and parent.GetName():  # Skip root
            self._check_account_hierarchy(parent, checked)
        
        # Check this account
        path = self.get_account_path(account)
        if path and path not in self.base_accounts_by_path:
            commodity = account.GetCommodity()
            comm_str = f"{commodity.get_namespace()}:{commodity.get_mnemonic()}" if commodity else "None"
            
            self.missing_accounts.append({
                'account': account,
                'path': path,
                'type': account.GetType(),
                'commodity': comm_str,
                'description': account.GetDescription()
            })
    
    def _analyze_transactions(self):
        """Analyze transactions to be imported."""
        # Get all transactions by iterating through accounts
        transactions = {}  # guid -> transaction
        
        for account in self.export_accounts.values():
            for split in account.GetSplitList():
                trans = split.GetParent()
                guid = trans.GetGUID().to_string()
                if guid not in transactions:
                    transactions[guid] = trans
        
        for trans in transactions.values():
            trans_info = {
                'transaction': trans,
                'guid': trans.GetGUID().to_string(),
                'date': trans.GetDate(),
                'description': trans.GetDescription(),
                'num_splits': trans.CountSplits(),
                'currency': trans.GetCurrency().get_mnemonic() if trans.GetCurrency() else 'None'
            }
            self.transactions_to_import.append(trans_info)
        
        if self.transactions_to_import:
            print(f"Transactions to Import ({len(self.transactions_to_import)}):")
            for i, trans_info in enumerate(self.transactions_to_import[:10], 1):
                date_obj = trans_info['date']
                date_str = date_obj.strftime('%Y-%m-%d') if isinstance(date_obj, datetime) else str(date_obj)
                desc = trans_info['description'] or "(no description)"
                print(f"  {i}. {date_str} - {desc} ({trans_info['num_splits']} splits)")
            
            if len(self.transactions_to_import) > 10:
                print(f"  ... and {len(self.transactions_to_import) - 10} more")
            print()
    
    def _print_summary(self):
        """Print summary of dry run."""
        print("=" * 70)
        print("SUMMARY")
        print("=" * 70)
        print(f"Commodities to create: {len(self.missing_commodities)}")
        print(f"Accounts to create: {len(self.missing_accounts)}")
        print(f"Transactions to import: {len(self.transactions_to_import)}")
        print()
    
    def create_missing_commodities(self):
        """Create missing commodities in base book."""
        if not self.missing_commodities:
            return
        
        print(f"Creating {len(self.missing_commodities)} missing commodities...")
        commodity_table = self.base_book.get_table()
        
        for commodity in self.missing_commodities:
            namespace = commodity.get_namespace()
            mnemonic = commodity.get_mnemonic()
            fullname = commodity.get_fullname()
            
            new_comm = GncCommodity(self.base_book, fullname, namespace, mnemonic)
            commodity_table.insert(new_comm)
            
            # Update our index
            key = (namespace, mnemonic)
            self.base_commodities[key] = new_comm
            
            print(f"  ✓ Created {namespace}:{mnemonic}")
        print()
    
    def create_missing_accounts(self):
        """Create missing accounts in base book."""
        if not self.missing_accounts:
            return
        
        print(f"Creating {len(self.missing_accounts)} missing accounts...")
        
        for acc_info in self.missing_accounts:
            self._create_account(acc_info)
        
        print()
    
    def _create_account(self, acc_info):
        """Create a single account with proper parent."""
        path = acc_info['path']
        
        # Check if already created (might have been created as parent)
        if path in self.base_accounts_by_path:
            return self.base_accounts_by_path[path]
        
        source_account = acc_info['account']
        
        # Find or create parent
        parent_account = self._get_or_create_parent(source_account)
        
        # Create the account
        new_account = Account(self.base_book)
        new_account.SetName(source_account.GetName())
        new_account.SetType(source_account.GetType())
        new_account.SetDescription(source_account.GetDescription())
        
        # Set commodity
        commodity = source_account.GetCommodity()
        if commodity:
            namespace = commodity.get_namespace()
            mnemonic = commodity.get_mnemonic()
            base_commodity = self.base_commodities.get((namespace, mnemonic))
            if base_commodity:
                new_account.SetCommodity(base_commodity)
        
        # Set parent
        parent_account.append_child(new_account)
        
        # Update our index
        guid = new_account.GetGUID().to_string()
        self.base_accounts[guid] = new_account
        self.base_accounts_by_path[path] = new_account
        
        print(f"  ✓ Created account: {path}")
        
        return new_account
    
    def _get_or_create_parent(self, account):
        """Get or create parent account."""
        parent = account.get_parent()
        
        if not parent or not parent.GetName():
            # Root account
            return self.base_book.get_root_account()
        
        parent_path = self.get_account_path(parent)
        
        # Check if parent exists
        if parent_path in self.base_accounts_by_path:
            return self.base_accounts_by_path[parent_path]
        
        # Parent doesn't exist, need to create it recursively
        parent_info = {
            'account': parent,
            'path': parent_path,
            'type': parent.GetType(),
            'commodity': '',
            'description': parent.GetDescription()
        }
        
        return self._create_account(parent_info)
    
    def import_transactions(self):
        """Import all transactions from export book."""
        if not self.transactions_to_import:
            print("No transactions to import.")
            return
        
        print(f"Importing {len(self.transactions_to_import)} transactions...")
        
        # Map export account GUIDs to base account objects
        account_map = {}
        for guid, export_account in self.export_accounts.items():
            path = self.get_account_path(export_account)
            if path in self.base_accounts_by_path:
                account_map[guid] = self.base_accounts_by_path[path]
        
        imported_count = 0
        for trans_info in self.transactions_to_import:
            source_trans = trans_info['transaction']
            
            # Create new transaction
            new_trans = Transaction(self.base_book)
            new_trans.BeginEdit()
            
            # Copy basic properties
            new_trans.SetDescription(source_trans.GetDescription())
            new_trans.SetNotes(source_trans.GetNotes())
            
            # Copy dates - GetDate returns datetime, we need to use the Secs methods
            # date_obj = source_trans.GetDate()
            date_posted = source_trans.RetDatePosted()
            date_entered = source_trans.RetDateEntered()
            new_trans.SetDatePostedSecs(date_posted)
            new_trans.SetDateEnteredSecs(date_entered)

            # Use TimeStamp setters for modern Python bindings
            # new_trans.SetDatePostedTS(source_trans.GetDate())
            
            # Set currency
            currency = source_trans.GetCurrency()
            if currency:
                namespace = currency.get_namespace()
                mnemonic = currency.get_mnemonic()
                base_currency = self.base_commodities.get((namespace, mnemonic))
                if base_currency:
                    new_trans.SetCurrency(base_currency)
            
            # Copy splits
            for split in source_trans.GetSplitList():
                # Get target account
                split_account_guid = split.GetAccount().GetGUID().to_string()
                target_account = account_map.get(split_account_guid)
                
                if not target_account:
                    print(f"  ⚠ Warning: Could not map account for split in transaction '{source_trans.GetDescription()}'")
                    continue

                new_split = Split(self.base_book)
                new_split.SetParent(new_trans) 
                new_split.SetAccount(target_account)

                new_split.SetValue(split.GetValue())
                new_split.SetAmount(split.GetAmount())

                new_split.SetMemo(split.GetMemo())
                new_split.SetReconcile(split.GetReconcile())
                
                # new_trans.AppendSplit(new_split)
            
            new_trans.CommitEdit()
            imported_count += 1
            
            if imported_count % 10 == 0:
                print(f"  Imported {imported_count}/{len(self.transactions_to_import)} transactions...")
        
        print(f"✓ Successfully imported {imported_count} transactions")
        print()
    
    def save_base_book(self):
        """Save the base book."""
        print("Saving base book...")
        self.base_session.save()
        print("✓ Base book saved successfully")

    def prepare_import(self):
        self.open_sessions()
        self.index_accounts()
        self.index_commodities()
        self.analyze_import()

    def execute_import(self):
        self.create_missing_commodities()
        self.create_missing_accounts()
        self.import_transactions()
        self.save_base_book()

    def ask_confirmation(self):
        print("\nDo you want to proceed with the import? (yes/no): ", end='')
        response = input().strip().lower()
        print()
        return response in ['yes', 'y']
    
    def run(self, confirm=True):
        try:
            self.prepare_import()
            if confirm and not self.ask_confirmation():
                print('Import cancelled.')
                return None
            print("Starting import process...\n")
            self.execute_import()
        finally:
            self.close_sessions()
