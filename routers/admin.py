import os
import json
import asyncio
import re
import pandas as pd
from datetime import datetime
from bson import ObjectId

from fastapi import APIRouter, Request, Form, UploadFile, File, BackgroundTasks
from fastapi.responses import JSONResponse, RedirectResponse, HTMLResponse
import io
import sys

from flash import flash
import ai_core

router = APIRouter()

scraper_state = {
    "status": "idle",
    "log": [],
    "inserted": 0,
    "found": 0,
    "started_at": None,
    "finished_at": None
}

def check_is_super_admin(user):
    return user and user.get("email", "") in ["mzaki2222@gmail.com", "yossif7zaki@gmail.com"]

async def require_admin(request: Request):
    user_id = request.session.get('user_id')
    if not user_id:
        return None
    db = request.app.state.mongo_db
    if db is None:
        return None
    user = await db.users.find_one({"_id": user_id})
    if user and (user.get("role") in ["admin", "super_admin"] or check_is_super_admin(user)):
        return user
    return None

@router.get("/admin", response_class=HTMLResponse)
async def admin_dashboard(request: Request, page: int = 1):
    current_admin = await require_admin(request)
    if not current_admin:
        flash(request, "Access denied. Admins only.", "danger")
        return RedirectResponse(url="/", status_code=303)
        
    db = request.app.state.mongo_db
    users = []
    async for u in db.users.find({}):
        users.append(u)
        
    per_page = 50
    total_courses = await db.courses.count_documents({})
    total_pages = (total_courses + per_page - 1) // per_page
    
    courses = []
    async for c in db.courses.find({}, {'_id': 0}).skip((page - 1) * per_page).limit(per_page):
        courses.append(c)
        
    pending_courses = []
    async for pc in db.submitted_courses.find({"status": "pending"}):
        pending_courses.append(pc)
        
    from core_templates import templates
    return templates.TemplateResponse(request=request, name='admin.html', context= {
        "request": request, 
        "users": users, 
        "courses": courses, 
        "page": page, 
        "total_pages": total_pages, 
        "pending_courses": pending_courses,
        "current_user": current_admin
    })

@router.post("/api/admin/approve_course")
async def approve_course(request: Request, background_tasks: BackgroundTasks):
    if not await require_admin(request):
        return JSONResponse({"success": False, "error": "Unauthorized"}, status_code=401)
        
    try:
        data = await request.json()
    except:
        data = {}
        
    course_id = data.get('course_id')
    if not course_id:
        return JSONResponse({"success": False, "error": "No course ID provided"}, status_code=400)
        
    db = request.app.state.mongo_db
    try:
        pending = await db.submitted_courses.find_one({"_id": ObjectId(course_id)})
    except:
        return JSONResponse({"success": False, "error": "Invalid course ID"}, status_code=400)
        
    if not pending:
        return JSONResponse({"success": False, "error": "Pending course not found"}, status_code=404)
        
    course_to_insert = pending.copy()
    course_to_insert.pop('_id', None)
    course_to_insert.pop('status', None)
    course_to_insert.pop('submitted_by', None)
    course_to_insert.pop('submitted_at', None)
    
    await db.courses.insert_one(course_to_insert)
    await db.submitted_courses.delete_one({"_id": ObjectId(course_id)})
    
    def reload_model():
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        ai_core.load_and_train_model(db)
        
    background_tasks.add_task(reload_model)
    return JSONResponse({"success": True, "message": "Course approved and added to catalog!"})

@router.post("/api/admin/reject_course")
async def reject_course(request: Request):
    if not await require_admin(request):
        return JSONResponse({"success": False, "error": "Unauthorized"}, status_code=401)
        
    try:
        data = await request.json()
    except:
        data = {}
        
    course_id = data.get('course_id')
    if not course_id:
        return JSONResponse({"success": False, "error": "No course ID provided"}, status_code=400)
        
    db = request.app.state.mongo_db
    try:
        result = await db.submitted_courses.delete_one({"_id": ObjectId(course_id)})
    except:
        return JSONResponse({"success": False, "error": "Invalid course ID"}, status_code=400)
        
    if result.deleted_count > 0:
        return JSONResponse({"success": True, "message": "Course rejected and deleted"})
    else:
        return JSONResponse({"success": False, "error": "Course not found"}, status_code=404)

@router.post("/api/delete_course")
async def delete_course(request: Request, background_tasks: BackgroundTasks):
    if not await require_admin(request):
        return JSONResponse({"success": False, "error": "Unauthorized"}, status_code=401)
        
    try:
        data = await request.json()
    except:
        data = {}
        
    course_url = data.get('url')
    if not course_url:
        return JSONResponse({"success": False, "error": "No course URL provided"}, status_code=400)
        
    db = request.app.state.mongo_db
    result = await db.courses.delete_one({"url": course_url})
    
    if result.deleted_count > 0:
        def reload_model():
            import asyncio
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            ai_core.load_and_train_model(db)
        background_tasks.add_task(reload_model)
        return JSONResponse({"success": True, "message": "Course deleted successfully"})
    else:
        return JSONResponse({"success": False, "error": "Course not found"}, status_code=404)

@router.post("/api/admin/toggle_user_role")
async def toggle_user_role(request: Request):
    current_admin = await require_admin(request)
    if not current_admin:
        return JSONResponse({"success": False, "error": "Unauthorized"}, status_code=401)
        
    try:
        data = await request.json()
    except:
        data = {}
        
    user_id = data.get('user_id')
    new_role = data.get('role')
    
    if not user_id or not new_role:
        return JSONResponse({"success": False, "error": "Missing parameters"}, status_code=400)
        
    db = request.app.state.mongo_db
    target_user = await db.users.find_one({"_id": user_id})
    if not target_user:
        return JSONResponse({"success": False, "error": "User not found"}, status_code=404)
        
    current_is_super = check_is_super_admin(current_admin)
    target_is_super = check_is_super_admin(target_user)
    
    if target_user.get('role') == 'admin' and new_role != 'admin':
        admin_count = await db.users.count_documents({"role": "admin"})
        if admin_count <= 1:
            return JSONResponse({"success": False, "error": "Security Restriction: Cannot demote the only remaining admin."}, status_code=403)
            
    if target_is_super and new_role not in ['admin', 'super_admin']:
        all_admins = []
        async for u in db.users.find({"role": {"$in": ["admin", "super_admin"]}}):
            all_admins.append(u)
        super_count = sum(1 for u in all_admins if check_is_super_admin(u))
        if super_count <= 1:
            return JSONResponse({"success": False, "error": "Security Restriction: Cannot demote the only remaining Super Admin."}, status_code=403)

    if (target_user.get('role') in ['admin', 'super_admin'] or target_is_super or new_role in ['admin', 'super_admin']):
        if not current_is_super:
            return JSONResponse({"success": False, "error": "Security Restriction: Only a Super Admin can promote, demote, or modify admin accounts."}, status_code=403)

    result = await db.users.update_one({"_id": user_id}, {"$set": {"role": new_role}})
    
    if result.modified_count > 0:
        return JSONResponse({"success": True})
    return JSONResponse({"success": False, "error": "Role already set to this value"}, status_code=400)

@router.post("/api/admin/toggle_premium")
async def toggle_premium(request: Request):
    if not await require_admin(request):
        return JSONResponse({"success": False, "error": "Unauthorized"}, status_code=401)
        
    try:
        data = await request.json()
    except:
        data = {}
        
    user_id = data.get('user_id')
    new_status = data.get('is_premium')
    
    if not user_id:
        return JSONResponse({"success": False, "error": "Missing user ID"}, status_code=400)
        
    db = request.app.state.mongo_db
    target_user = await db.users.find_one({"_id": user_id})
        
    if not target_user:
        return JSONResponse({"success": False, "error": "User not found"}, status_code=404)
        
    await db.users.update_one({"_id": user_id}, {"$set": {"is_premium": new_status}})
    return JSONResponse({"success": True, "message": "Premium status updated"})

@router.post("/api/admin/delete_user")
async def delete_user_admin(request: Request):
    current_admin = await require_admin(request)
    if not current_admin:
        return JSONResponse({"success": False, "error": "Unauthorized"}, status_code=401)
        
    try:
        data = await request.json()
    except:
        data = {}
        
    user_id = data.get('user_id')
    if not user_id:
        return JSONResponse({"success": False, "error": "Missing user ID"}, status_code=400)
        
    if user_id == current_admin['_id']:
        return JSONResponse({"success": False, "error": "You cannot delete your own admin account from here."}, status_code=403)
        
    db = request.app.state.mongo_db
    target_user = await db.users.find_one({"_id": user_id})
    if not target_user:
        return JSONResponse({"success": False, "error": "User not found"}, status_code=404)
        
    current_is_super = check_is_super_admin(current_admin)
    target_is_super = check_is_super_admin(target_user)
    
    if target_user.get('role') == 'admin':
        admin_count = await db.users.count_documents({"role": "admin"})
        if admin_count <= 1:
            return JSONResponse({"success": False, "error": "Security Restriction: Cannot delete the only remaining admin."}, status_code=403)
            
    if target_is_super:
        all_admins = []
        async for u in db.users.find({"role": {"$in": ["admin", "super_admin"]}}):
            all_admins.append(u)
        super_count = sum(1 for u in all_admins if check_is_super_admin(u))
        if super_count <= 1:
            return JSONResponse({"success": False, "error": "Security Restriction: Cannot delete the only remaining Super Admin."}, status_code=403)
            
    if target_user.get('role') in ['admin', 'super_admin'] or target_is_super:
        if not current_is_super:
            return JSONResponse({"success": False, "error": "Security Restriction: Only a Super Admin can delete another admin account."}, status_code=403)
            
    result = await db.users.delete_one({"_id": user_id})
    if result.deleted_count > 0:
        return JSONResponse({"success": True})
    return JSONResponse({"success": False, "error": "User not found"}, status_code=404)

@router.post("/api/admin/add_course")
async def add_course(request: Request, background_tasks: BackgroundTasks):
    if not await require_admin(request):
        return JSONResponse({"success": False, "error": "Unauthorized"}, status_code=401)
        
    try:
        data = await request.json()
    except:
        data = {}
        
    title = data.get('title')
    provider = data.get('provider')
    url = data.get('url')
    stars = data.get('stars', 0.0)
    
    if not title or not provider or not url:
        return JSONResponse({"success": False, "error": "Missing required fields"}, status_code=400)
        
    db = request.app.state.mongo_db
    if await db.courses.find_one({"url": url}):
        return JSONResponse({"success": False, "error": "Course with this URL already exists"}, status_code=400)
        
    new_course = {
        "title": title,
        "provider": provider,
        "url": url,
        "stars": float(stars),
        "track": "General CS"
    }
    await db.courses.insert_one(new_course)
    
    def reload_model():
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        ai_core.load_and_train_model(db)
    background_tasks.add_task(reload_model)
    return JSONResponse({"success": True})

@router.post("/api/admin/edit_course")
async def edit_course(request: Request, background_tasks: BackgroundTasks):
    if not await require_admin(request):
        return JSONResponse({"success": False, "error": "Unauthorized"}, status_code=401)
        
    try:
        data = await request.json()
    except:
        data = {}
        
    old_url = data.get('old_url')
    title = data.get('title')
    provider = data.get('provider')
    url = data.get('url')
    stars = data.get('stars', 0.0)
    
    if not old_url or not title or not provider or not url:
        return JSONResponse({"success": False, "error": "Missing required fields"}, status_code=400)
        
    db = request.app.state.mongo_db
    if old_url != url and await db.courses.find_one({"url": url}):
        return JSONResponse({"success": False, "error": "A course with the new URL already exists"}, status_code=400)
        
    update_data = {
        "title": title,
        "provider": provider,
        "url": url,
        "stars": float(stars)
    }
    
    result = await db.courses.update_one({"url": old_url}, {"$set": update_data})
    if result.modified_count > 0 or result.matched_count > 0:
        def reload_model():
            import asyncio
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            ai_core.load_and_train_model(db)
        background_tasks.add_task(reload_model)
        return JSONResponse({"success": True})
        
    return JSONResponse({"success": False, "error": "Course not found"}, status_code=404)

def _run_scraper_background(db):
    global scraper_state
    try:
        import scraper
        scraper_state["status"] = "running"
        scraper_state["log"] = ["[Scraper] Starting live scraper — this may take several minutes..."]
        scraper_state["inserted"] = 0
        scraper_state["found"] = 0
        scraper_state["started_at"] = datetime.utcnow().isoformat()
        scraper_state["finished_at"] = None

        old_stdout = sys.stdout
        sys.stdout = captured = io.StringIO()

        try:
            scraper.run_scraper()
        finally:
            sys.stdout = old_stdout

        output = captured.getvalue()
        lines = [l for l in output.splitlines() if l.strip()]
        scraper_state["log"] = lines[-80:]

        for line in lines:
            if "Inserted" in line and "new courses" in line:
                try:
                    parts = line.split()
                    idx = parts.index("Inserted")
                    scraper_state["inserted"] = int(parts[idx + 1])
                except Exception:
                    pass
            if "Fetched" in line and "courses total" in line:
                try:
                    parts = line.split()
                    scraper_state["found"] = int(parts[1])
                except Exception:
                    pass

        scraper_state["log"].append(f"[Scraper] ✅ Done! Found {scraper_state['found']} courses, inserted {scraper_state['inserted']} new ones.")
        scraper_state["status"] = "done"

        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        ai_core.load_and_train_model(db)

    except Exception as exc:
        scraper_state["log"].append(f"[Scraper] ❌ Error: {exc}")
        scraper_state["status"] = "error"
    finally:
        scraper_state["finished_at"] = datetime.utcnow().isoformat()


@router.post("/api/admin/run_scraper")
async def api_run_scraper(request: Request, background_tasks: BackgroundTasks):
    if not await require_admin(request):
        return JSONResponse({"success": False, "error": "Unauthorized"}, status_code=401)
        
    global scraper_state
    if scraper_state.get("status") == "running":
        return JSONResponse({"success": False, "error": "Scraper is already running."}, status_code=400)

    scraper_state = {
        "status": "starting",
        "log": ["[Scraper] Initializing..."],
        "inserted": 0,
        "found": 0,
        "started_at": datetime.utcnow().isoformat(),
        "finished_at": None,
    }
    db = request.app.state.mongo_db
    background_tasks.add_task(_run_scraper_background, db)
    return JSONResponse({"success": True, "message": "Scraper started in background."})

@router.get("/api/admin/scraper_status")
async def api_scraper_status(request: Request):
    if not await require_admin(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
        
    global scraper_state
    return JSONResponse({
        "status": scraper_state.get("status", "idle"),
        "log": scraper_state.get("log", []),
        "inserted": scraper_state.get("inserted", 0),
        "found": scraper_state.get("found", 0),
        "started_at": scraper_state.get("started_at"),
        "finished_at": scraper_state.get("finished_at"),
    })

youtube_state = {"status": "idle", "log": [], "inserted": 0, "found": 0, "started_at": None, "finished_at": None}

def _run_youtube_import_background(db, max_queries):
    global youtube_state
    try:
        import youtube_importer
        youtube_state["status"] = "running"
        result = youtube_importer.run_import(max_queries, log=youtube_state["log"].append)
        youtube_state["found"] = result["found"]
        youtube_state["inserted"] = result["inserted"]
        youtube_state["log"].append(f"[YouTube] ✅ Done! Kept {result['found']} CS items, inserted {result['inserted']} new ones.")
        youtube_state["status"] = "done"

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        ai_core.load_and_train_model(db)
    except Exception as exc:
        youtube_state["log"].append(f"[YouTube] ❌ Error: {exc}")
        youtube_state["status"] = "error"
    finally:
        youtube_state["finished_at"] = datetime.utcnow().isoformat()

@router.post("/api/admin/import_youtube")
async def api_import_youtube(request: Request, background_tasks: BackgroundTasks):
    if not await require_admin(request):
        return JSONResponse({"success": False, "error": "Unauthorized"}, status_code=401)

    global youtube_state
    if youtube_state.get("status") == "running":
        return JSONResponse({"success": False, "error": "YouTube import is already running."}, status_code=400)
    if not os.environ.get("YOUTUBE_API_KEY"):
        return JSONResponse({"success": False, "error": "YOUTUBE_API_KEY is not configured."}, status_code=400)

    try:
        data = await request.json()
    except:
        data = {}
    # Each query costs ~300 quota units of the 10,000/day default
    max_queries = max(1, min(int(data.get("max_queries", 10) or 10), 30))

    youtube_state = {
        "status": "starting",
        "log": [f"[YouTube] Initializing import ({max_queries} queries)..."],
        "inserted": 0,
        "found": 0,
        "started_at": datetime.utcnow().isoformat(),
        "finished_at": None,
    }
    db = request.app.state.mongo_db
    background_tasks.add_task(_run_youtube_import_background, db, max_queries)
    return JSONResponse({"success": True, "message": "YouTube import started in background."})

@router.get("/api/admin/youtube_status")
async def api_youtube_status(request: Request):
    if not await require_admin(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    return JSONResponse(youtube_state)

from cs_filter import is_cs_related

_CSV_COLUMN_MAP = {
    "title": "title", "name": "title", "course_name": "title", "course title": "title", "coursetitle": "title",
    "url": "url", "link": "url", "course_url": "url", "courseurl": "url", "course link": "url",
    "provider": "provider", "platform": "provider", "source": "provider", "organization": "provider", "institution": "provider",
    "stars": "stars", "rating": "stars", "score": "stars", "average rating": "stars", "avg_rating": "stars",
    "content_text": "content_text", "description": "content_text", "desc": "content_text", "about": "content_text", "content": "content_text", "overview": "content_text",
    "ratings_count": "ratings_count", "num_ratings": "ratings_count", "reviews": "ratings_count", "review_count": "ratings_count",
}

def _clean_csv_dataframe(raw_df):
    col_mapping = {}
    for col in raw_df.columns:
        canon = _CSV_COLUMN_MAP.get(col.strip().lower())
        if canon:
            col_mapping[canon] = col

    title_col = col_mapping.get("title")
    if not title_col:
        for col in raw_df.columns:
            if "title" in col.lower() or "name" in col.lower():
                title_col = col
                break
    if not title_col:
        raise ValueError("Could not detect a 'title' or 'name' column in the CSV.")

    desc_col = col_mapping.get("content_text")
    if not desc_col:
        for col in raw_df.columns:
            if any(k in col.lower() for k in ["desc", "about", "content", "overview"]):
                desc_col = col
                break

    url_col = col_mapping.get("url")
    if not url_col:
        for col in raw_df.columns:
            if "url" in col.lower() or "link" in col.lower():
                url_col = col
                break

    provider_col = col_mapping.get("provider")
    if not provider_col:
        for col in raw_df.columns:
            if any(k in col.lower() for k in ["provider", "platform", "source", "org", "inst"]):
                provider_col = col
                break

    clean_records = []
    
    for idx, row in raw_df.iterrows():
        title = str(row.get(title_col, '')).strip() if title_col in raw_df.columns else ''
        desc = str(row.get(desc_col, '')).strip() if desc_col in raw_df.columns else ''
        url = str(row.get(url_col, '')).strip() if url_col in raw_df.columns else ''
        
        if not title or title.lower() == 'nan': continue
        if not desc or desc.lower() == 'nan': desc = "No description provided."
        if not url or url.lower() == 'nan': url = ""

        if not is_cs_related(title, desc): continue

        stars = 0.0
        if 'Stars' in raw_df.columns and pd.notnull(row.get('Stars')):
            try: stars = float(row.get('Stars'))
            except: pass
        
        if (stars == 0.0 or stars is None) and 'Rating' in raw_df.columns and pd.notnull(row.get('Rating')):
            val = str(row.get('Rating')).strip()
            if 'stars' in val.lower():
                try: stars = float(val.lower().replace('stars', '').strip())
                except: pass
            else:
                try:
                    f_val = float(val)
                    if 0.0 <= f_val <= 5.0: stars = f_val
                except: pass
                    
        mapped_stars_col = col_mapping.get("stars")
        if (stars == 0.0 or stars is None) and mapped_stars_col and mapped_stars_col in raw_df.columns and pd.notnull(row.get(mapped_stars_col)):
            try: stars = float(row.get(mapped_stars_col))
            except: pass

        ratings_count = 0
        possible_rating_count_cols = ['Number of ratings', 'Number of Reviews', 'Rating', 'Number of viewers']
        found_count = False
        for col in possible_rating_count_cols:
            if col in raw_df.columns and pd.notnull(row.get(col)):
                val = str(row.get(col)).strip()
                if col == 'Rating' and 'stars' in val.lower(): continue
                val_clean = val.replace(',', '').replace(' ', '').replace('+', '').strip()
                try:
                    ratings_count = int(float(val_clean))
                    found_count = True
                    break
                except: pass
        
        mapped_count_col = col_mapping.get("ratings_count")
        if not found_count and mapped_count_col and mapped_count_col in raw_df.columns and pd.notnull(row.get(mapped_count_col)):
            val = str(row.get(mapped_count_col)).strip()
            val_clean = val.replace(',', '').replace(' ', '').replace('+', '').strip()
            try: ratings_count = int(float(val_clean))
            except: pass

        provider = "Unknown"
        if provider_col and provider_col in raw_df.columns and pd.notnull(row.get(provider_col)):
            provider = str(row.get(provider_col)).strip()
        
        for col in ['University', 'Site', 'School']:
            if col in raw_df.columns and pd.notnull(row.get(col)) and str(row.get(col)).strip():
                provider = str(row.get(col)).strip()
                break

        clean_records.append({
            "title": title, "url": url, "provider": provider, "content_text": desc,
            "stars": min(max(stars, 0.0), 5.0), "ratings_count": ratings_count
        })

    df_clean = pd.DataFrame(clean_records)
    if df_clean.empty: return df_clean
    return df_clean.drop_duplicates(subset=["title"])

@router.post("/api/admin/upload_csv")
async def upload_csv(request: Request, background_tasks: BackgroundTasks, file: UploadFile = File(None)):
    if not await require_admin(request):
        return JSONResponse({"success": False, "error": "Unauthorized"}, status_code=401)
        
    if not file:
        return JSONResponse({"success": False, "error": "No file uploaded."}, status_code=400)

    if not file.filename or not file.filename.lower().endswith('.csv'):
        return JSONResponse({"success": False, "error": "Please upload a .csv file."}, status_code=400)

    content = await file.read()
    try:
        raw_df = pd.read_csv(io.BytesIO(content), encoding='utf-8', on_bad_lines='skip')
    except Exception:
        try:
            raw_df = pd.read_csv(io.BytesIO(content), encoding='latin-1', on_bad_lines='skip')
        except Exception as e:
            return JSONResponse({"success": False, "error": f"Could not parse CSV: {e}"}, status_code=400)

    try:
        df_clean = _clean_csv_dataframe(raw_df)
    except ValueError as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=400)

    if df_clean.empty:
        return JSONResponse({"success": False, "error": "No CS-related courses found in the uploaded CSV."}, status_code=400)

    db = request.app.state.mongo_db
    if db is None:
        return JSONResponse({"success": False, "error": "Database connection error."}, status_code=500)

    inserted = 0
    skipped = 0
    for _, row in df_clean.iterrows():
        doc = {
            "title": row["title"], "url": row["url"], "provider": row["provider"],
            "content_text": row["content_text"], "stars": float(row["stars"]), "ratings_count": int(row["ratings_count"]),
        }
        result = await db.courses.update_one({"title": doc["title"]}, {"$set": doc}, upsert=True)
        if result.upserted_id: inserted += 1
        else: skipped += 1

    if inserted > 0:
        def sync_and_reload():
            import asyncio
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                os.makedirs("datasets", exist_ok=True)
                db_path = "datasets/CS_Dataset_Phase2.json"
                xlsx_path = "datasets/CS_Dataset_Phase2.xlsx"
                
                async def fetch_all():
                    return [c async for c in db.courses.find({}, {'_id': 0})]
                all_courses = loop.run_until_complete(fetch_all())
                
                with open(db_path, 'w') as f:
                    json.dump(all_courses, f, indent=4)
                pd.DataFrame(all_courses).to_excel(xlsx_path, index=False)
            except Exception as file_err:
                print(f"Failed to sync backups to local files: {file_err}")
                
            ai_core.load_and_train_model(db)
            
        background_tasks.add_task(sync_and_reload)

    return JSONResponse({
        "success": True,
        "inserted": inserted,
        "skipped": skipped,
        "total_in_file": len(df_clean),
        "message": f"Imported {inserted} new courses, skipped {skipped} duplicates."
    })
