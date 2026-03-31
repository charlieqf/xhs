import os
import sys
import subprocess

def main():
    # Resolve absolute paths based on the script location
    script_dir = os.path.dirname(os.path.abspath(__file__))
    base_dir = os.path.dirname(script_dir)
    
    post_md = os.path.join(script_dir, "post1.md")
    image_path = os.path.join(script_dir, "Gemini_Generated_Image_ppe3nwppe3nwppe3.png")
    pipeline_script = os.path.join(base_dir, "scripts", "publish_pipeline.py")

    if not os.path.exists(post_md):
        print(f"Error: Markdown file not found at {post_md}")
        sys.exit(1)
        
    if not os.path.exists(image_path):
        print(f"Error: Image not found at {image_path}")
        sys.exit(1)

    print(f"Reading post from: {post_md}")
    with open(post_md, "r", encoding="utf-8") as f:
        lines = f.readlines()
        
    if not lines:
        print("Error: Markdown file is empty.")
        sys.exit(1)

    # The first line is the title, the rest is content
    title = lines[0].strip().lstrip("#").strip()
    content = "".join(lines[1:]).strip()

    # Create temporary files for title and content to avoid command line length / quotation issues
    title_file = os.path.join(script_dir, "temp_title.txt")
    content_file = os.path.join(script_dir, "temp_content.txt")
    
    with open(title_file, "w", encoding="utf-8") as f:
        f.write(title)
        
    with open(content_file, "w", encoding="utf-8") as f:
        f.write(content)

    print(f"Title: {title}")
    print(f"Image: {image_path}")
    print("\nStarting publish pipeline...")

    # Build command using --title-file and --content-file
    cmd = [
        sys.executable,
        pipeline_script,
        "--headless",
        "--title-file", title_file,
        "--content-file", content_file,
        "--images", image_path
    ]

    try:
        # Run the pipeline
        result = subprocess.run(cmd)
        
        if result.returncode == 0:
            print("\n✅ Successfully published the post!")
        else:
            print(f"\n❌ Pipeline failed with return code {result.returncode}")
            
    finally:
        # Cleanup temporary files
        if os.path.exists(title_file):
            os.remove(title_file)
        if os.path.exists(content_file):
            os.remove(content_file)

if __name__ == "__main__":
    main()
