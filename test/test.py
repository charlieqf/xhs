import sys
import os
import json

# Setup paths
script_dir = os.path.dirname(os.path.abspath(__file__))
base_dir = os.path.dirname(script_dir)
sys.path.insert(0, os.path.join(base_dir, "scripts"))

from cdp_publish import XiaohongshuPublisher

def main():
    print("Initializing CDP Publisher...")
    publisher = XiaohongshuPublisher()
    
    # 1. Connect to Chrome tab
    print("Connecting to Chrome...")
    try:
        publisher.connect()
    except Exception as e:
        print(f"Error connecting to Chrome: {e}")
        print("Make sure Chrome is running with remote debugging port 9222.")
        sys.exit(1)
        
    # Check login on home page (which is used for reading feeds)
    if not publisher.check_home_login(wait_seconds=5.0):
        print("Error: Not logged into Xiaohongshu web. Please log in first.")
        sys.exit(1)
        
    # 2. Search feeds for the keyword
    keyword = "相亲平台"
    print(f"Searching for feeds related to '{keyword}'...")
    try:
        search_results = publisher.search_feeds(keyword=keyword)
    except Exception as e:
        print(f"Error during search: {e}")
        sys.exit(1)
        
    feeds = search_results.get("feeds", [])
    if not feeds:
        print("No feeds found.")
        sys.exit(0)
        
    # We want the top 10 feeds
    top_10_feeds = feeds[:10]
    print(f"\nFound total {len(feeds)} feeds. Will extract comments for the top {len(top_10_feeds)}...")
    
    all_details = []
    
    # 3. Iterate top 10 feeds and get feed detail (comments limited to 10)
    for idx, feed in enumerate(top_10_feeds, start=1):
        feed_id = feed.get("id")
        xsec_token = feed.get("xsecToken") or feed.get("noteCard", {}).get("user", {}).get("xsecToken")
        title = feed.get("noteCard", {}).get("displayTitle", "Unknown")
        
        print(f"\n[{idx}/10] Fetching details for: {title} (ID: {feed_id})")
        
        if not feed_id or not xsec_token:
            print("  -> Skipping: missing feed_id or xsec_token.")
            continue
            
        try:
            # We enforce limits here to get up to 10 comments quickly
            detail = publisher.get_feed_detail(
                feed_id=feed_id,
                xsec_token=xsec_token,
                load_all_comments=False,
                limit=10, 
                click_more_replies=False,
                reply_limit=0,
                scroll_speed='fast'
            )
            all_details.append({
                "feed_metadata": feed,
                "feed_detail": detail
            })
            print(f"  -> Successfully extracted {len(detail.get('comments', []))} comments.")
        except Exception as e:
            print(f"  -> Failed to fetch detail: {e}")
            
    # 4. Save results
    out_file = os.path.join(script_dir, "comments.json")
    print(f"\nSaving all results to: {out_file} ...")
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(all_details, f, ensure_ascii=False, indent=2)
        
    print("🎉 All done!")

if __name__ == "__main__":
    main()