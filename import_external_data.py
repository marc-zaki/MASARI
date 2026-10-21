import pandas as pd
import json
import os
import re

from cs_filter import is_cs_related

def import_csv(file_path, provider_name, title_col, desc_col, url_col, default_url_prefix=""):
    """
    Parses an external CSV, strictly filters for CS-related courses, and merges them.
    """
    os.makedirs("datasets", exist_ok=True)
    db_path = "datasets/CS_Dataset_Phase2.json"
    xlsx_path = "datasets/CS_Dataset_Phase2.xlsx"
    
    if not os.path.exists(file_path):
        print(f"Error: External file '{file_path}' not found.")
        return
        
    print(f"Reading '{file_path}'...")
    try:
        ext_df = pd.read_csv(file_path)
    except Exception as e:
        print(f"Failed to read CSV: {e}")
        return
        
    print(f"Found {len(ext_df)} records. Filtering strictly for Computer Science...")
    
    new_courses = []
    skipped_count = 0
    
    for idx, row in ext_df.iterrows():
        title = str(row.get(title_col, '')).strip()
        desc = str(row.get(desc_col, '')).strip()
        url = str(row.get(url_col, '')).strip()
        
        # Validation
        if not title or title.lower() == 'nan':
            continue
        if not desc or desc.lower() == 'nan':
            desc = "No description provided."
        if not url or url.lower() == 'nan':
            url = default_url_prefix
            
        # Check relevance
        if not is_cs_related(title, desc):
            skipped_count += 1
            continue
            
        # Extract stars and rating count if present in the CSV
        stars = 0.0
        ratings_count = 0

        # Try to find star rating (value from 1 to 5)
        if 'Stars' in ext_df.columns and pd.notnull(row.get('Stars')):
            try:
                stars = float(row.get('Stars'))
            except:
                pass
        if (stars == 0.0 or stars is None) and 'Rating' in ext_df.columns and pd.notnull(row.get('Rating')):
            val = str(row.get('Rating')).strip()
            if 'stars' in val.lower():
                try:
                    stars = float(val.lower().replace('stars', '').strip())
                except:
                    pass
            else:
                try:
                    f_val = float(val)
                    if 0.0 <= f_val <= 5.0:
                        stars = f_val
                except:
                    pass

        # Try to find ratings count
        possible_rating_count_cols = ['Number of ratings', 'Number of Reviews', 'Rating', 'Number of viewers']
        for col in possible_rating_count_cols:
            if col in ext_df.columns and pd.notnull(row.get(col)):
                val = str(row.get(col)).strip()
                if col == 'Rating' and 'stars' in val.lower():
                    continue
                val_clean = val.replace(',', '').replace(' ', '').replace('+', '').strip()
                try:
                    ratings_count = int(float(val_clean))
                    break
                except:
                    pass

        # Determine actual provider dynamically from row keys if available (e.g. University, Site, School)
        actual_provider = provider_name
        if 'University' in row and pd.notnull(row.get('University')) and str(row.get('University')).strip():
            actual_provider = str(row.get('University')).strip()
        elif 'Site' in row and pd.notnull(row.get('Site')) and str(row.get('Site')).strip():
            actual_provider = str(row.get('Site')).strip()
        elif 'School' in row and pd.notnull(row.get('School')) and str(row.get('School')).strip():
            actual_provider = str(row.get('School')).strip()

        new_courses.append({
            "provider": actual_provider,
            "title": title,
            "content_text": desc,
            "url": url,
            "raw_reviews": [],
            "review_summary": "",
            "stars": stars,
            "ratings_count": ratings_count
        })
        
    print(f"Filter Complete: Kept {len(new_courses)} CS courses. Skipped {skipped_count} non-CS courses.")
    
    if not new_courses:
        print("No matching CS courses found. Database was not updated.")
        return

    # Load current database
    if os.path.exists(db_path):
        try:
            with open(db_path, 'r') as f:
                current_data = json.load(f)
            print(f"Loaded existing database with {len(current_data)} courses.")
        except Exception as e:
            print(f"Could not read existing database, starting fresh: {e}")
            current_data = []
    else:
        current_data = []
        
    # Merge and eliminate duplicates by title
    combined = current_data + new_courses
    
    seen_titles = set()
    deduped = []
    for course in combined:
        title_lower = course['title'].lower().strip()
        if title_lower not in seen_titles:
            seen_titles.add(title_lower)
            deduped.append(course)
            
    print(f"Total unique CS courses in database after merging: {len(deduped)}")
    
    # Save back
    try:
        with open(db_path, 'w') as f:
            json.dump(deduped, f, indent=4)
        print(f"Saved merged database to '{db_path}'")
        
        pd.DataFrame(deduped).to_excel(xlsx_path, index=False)
        print(f"Saved Excel backup to '{xlsx_path}'")
        
        # Sync to MongoDB if available
        try:
            import pymongo
            mongo_uri = os.environ.get("MONGO_URI", "mongodb://localhost:27017/")
            print(f"Syncing updated dataset to MongoDB ({mongo_uri})...")
            client = pymongo.MongoClient(mongo_uri, serverSelectionTimeoutMS=2000)
            client.server_info()
            db = client["cs_recommender"]
            col = db["courses"]
            
            # Clear old records and write new ones to ensure perfect sync
            col.delete_many({})
            col.insert_many(deduped)
            print(f"Successfully synced {len(deduped)} courses to MongoDB collection 'cs_recommender.courses'!")
        except Exception as db_err:
            print(f"MongoDB Sync Warning: Could not write to MongoDB ({db_err}). Dataset was saved to local JSON/Excel files.")
            
        print("\n🎉 Success! Restart your Flask server to load the new courses.")
    except Exception as e:
        print(f"Failed to save database: {e}")

if __name__ == "__main__":
    print("--- Kaggle / External CS CSV Importer Utility ---")
    print("This utility will automatically FILTER OUT non-CS courses (e.g. music, finance, cooking).")
    
    # Helper to resolve dataset path inside datasets/ folder or root directory
    def get_valid_path(filename):
        if os.path.exists(filename):
            return filename
        datasets_path = os.path.join("datasets", filename)
        if os.path.exists(datasets_path):
            return datasets_path
        return None
        
    # Import Udemy Tech CSV if present
    udemy_file = get_valid_path('udemy_tech.csv')
    if udemy_file:
        print(f"\nDetected '{udemy_file}'! Starting auto-import...")
        import_csv(
            file_path=udemy_file,
            provider_name='Udemy',
            title_col='Title',
            desc_col='Summary',
            url_col='Link'
        )
    else:
        print(f"\nCould not find 'udemy_tech.csv' in root or datasets/ directory.")
        
    # Import Coursera English CSV if present
    coursera_file = get_valid_path('courses_en.csv')
    if coursera_file:
        print(f"\nDetected '{coursera_file}'! Starting auto-import...")
        import_csv(
            file_path=coursera_file,
            provider_name='Coursera',
            title_col='name',
            desc_col='content',
            url_col='url'
        )
    else:
        print(f"\nCould not find 'courses_en.csv' in root or datasets/ directory.")

    # Import EdX CSV if present
    edx_file = get_valid_path('EdX.csv')
    if edx_file:
        print(f"\nDetected '{edx_file}'! Starting auto-import...")
        import_csv(
            file_path=edx_file,
            provider_name='EdX',
            title_col='Name',
            desc_col='Course Description',
            url_col='Link'
        )
    else:
        print(f"\nCould not find 'EdX.csv' in root or datasets/ directory.")

    # Import Online Courses CSV if present
    online_file = get_valid_path('Online_Courses.csv')
    if online_file:
        print(f"\nDetected '{online_file}'! Starting auto-import...")
        import_csv(
            file_path=online_file,
            provider_name='Online Courses',
            title_col='Title',
            desc_col='Short Intro',
            url_col='URL'
        )
    else:
        print(f"\nCould not find 'Online_Courses.csv' in root or datasets/ directory.")
