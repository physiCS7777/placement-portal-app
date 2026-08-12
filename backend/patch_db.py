import sqlite3
import os

def hunt_and_patch():
    possible_dirs = ['.', 'instance']
    patched_any = False

    print("Searching for the active database file...")

    for folder in possible_dirs:
        if not os.path.exists(folder):
            continue
        for file in os.listdir(folder):
            if file.endswith('.db') or file.endswith('.sqlite'):
                db_path = os.path.join(folder, file)
                
                try:
                    conn = sqlite3.connect(db_path)
                    cursor = conn.cursor()
                    
                    # 🌟 FIXED: Checking for the plural 'placement_drives' table
                    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='placement_drives';")
                    table_exists = cursor.fetchone()
                    
                    if table_exists:
                        print(f"🎯 Found active database at: {db_path}")
                        # 🌟 FIXED: Altering the plural table name
                        cursor.execute("ALTER TABLE placement_drives ADD COLUMN allowed_stream TEXT DEFAULT 'All Streams';")
                        conn.commit()
                        print(f"✅ Success! Added 'allowed_stream' column to 'placement_drives' with zero data loss.")
                        patched_any = True
                        conn.close()
                        return
                    
                    conn.close()
                except sqlite3.OperationalError as e:
                    if "duplicate column name" in str(e).lower():
                        print(f"🎯 Database at {db_path} is already fully patched!")
                        patched_any = True
                        return
                except Exception:
                    pass

    if not patched_any:
        print("❌ Could not locate the database file containing 'placement_drives'.")

if __name__ == "__main__":
    hunt_and_patch()