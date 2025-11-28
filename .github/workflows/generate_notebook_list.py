import os
import json
import nbformat
import yaml
import subprocess
from urllib.parse import urlparse
import pathlib
import re
import base64
from PIL import Image
from io import BytesIO

ROOT_DIR = os.path.abspath(".")
OUTPUT_FILE = "notebooks.json"
NOTEBOOK_DIR = "notebooks"
SUBMODULE_ROOT = "external_notebooks"
JHUB_INSTANCE = "url.to.jhubinstance"
IGNORE_FOLDERS = ["venv", ".git", ".github", "_build", "_data", "dist"]
DEF_ORG = "submodule-org-fallback"
DEF_REPO = "example-viewer"
OUTPUT_DIR = "_build/html/build/_assets/previews"

def extract_image_with_fallback(nb, notebook_rel_path, output_dir=OUTPUT_DIR, target_width=300):
    """
    Extract and process image with priority system.
    Priority:
    1. notebook.metadata.tags.image (from notebook metadata)
    2. Last image found in markdown cells (Markdown or MyST syntax)
    3. Last image output in code cells
    
    All images are resized to target_width while preserving aspect ratio.
    """
    os.makedirs(output_dir, exist_ok=True)
    
    # Priority 1: Check notebook metadata for image
    nb_metadata = nb.get('metadata', {})
    tags_metadata = nb_metadata.get('tags', {})
    
    if 'image' in tags_metadata:
        metadata_image_url = tags_metadata['image']
        print(f"[info] Found image in metadata: {metadata_image_url}")
        
        # Handle relative paths
        if not metadata_image_url.startswith(('http://', 'https://', 'data:')):
            notebook_dir = os.path.dirname(notebook_rel_path)
            image_abs_path = os.path.normpath(os.path.join(notebook_dir, metadata_image_url))
            
            if os.path.exists(image_abs_path):
                try:
                    return process_and_save_image(image_abs_path, notebook_rel_path, output_dir, target_width)
                except Exception as e:
                    print(f"[warn] Couldn't load/resize metadata image for {notebook_rel_path}: {e}")
        # Handle HTTP/HTTPS URLs
        elif metadata_image_url.startswith(('http://', 'https://')):
            try:
                import requests
                response = requests.get(metadata_image_url, timeout=10)
                response.raise_for_status()
                image_bytes = response.content
                return process_and_save_image_from_bytes(image_bytes, notebook_rel_path, output_dir, target_width)
            except Exception as e:
                print(f"[warn] Couldn't download/resize metadata image from URL {metadata_image_url}: {e}")
        # Handle data URIs (base64 encoded images)
        elif metadata_image_url.startswith('data:'):
            try:
                # Extract base64 data from data URI
                header, encoded = metadata_image_url.split(',', 1)
                image_bytes = base64.b64decode(encoded)
                return process_and_save_image_from_bytes(image_bytes, notebook_rel_path, output_dir, target_width)
            except Exception as e:
                print(f"[warn] Couldn't decode/resize metadata image from data URI: {e}")
    
    # Priority 2: Check markdown cells for images (keep last found)
    found_images = []
    for cell in nb.cells:
        if cell.cell_type == "markdown":
            lines = cell.source.splitlines()
            for line in lines:
                # Match Markdown image: ![alt](path)
                md_img = re.findall(r'!\[.*?\]\((.*?)\)', line)
                if md_img:
                    found_images.extend(md_img)
                # Match MyST figure directive: :::{figure} ./image.png
                myst_img = re.findall(r':::\{figure\}\s+(.*?)\s*$', line)
                if myst_img:
                    found_images.extend(myst_img)

    if found_images:
        last_image_rel = found_images[-1].strip()
        notebook_dir = os.path.dirname(notebook_rel_path)
        image_abs_path = os.path.normpath(os.path.join(notebook_dir, last_image_rel))
        print(f"[info] Found image in markdown: {image_abs_path}")

        if os.path.exists(image_abs_path):
            try:
                return process_and_save_image(image_abs_path, notebook_rel_path, output_dir, target_width)
            except Exception as e:
                print(f"[warn] Couldn't load/resize markdown image for {notebook_rel_path}: {e}")
    
    # Priority 3: Check code output (keep last found)
    for cell in reversed(nb.cells):
        if cell.cell_type == "code":
            for output in reversed(cell.get("outputs", [])):
                data = output.get("data", {})
                if "image/png" in data:
                    b64 = data["image/png"]
                    image_bytes = base64.b64decode(b64)

                    try:
                        return process_and_save_image_from_bytes(image_bytes, notebook_rel_path, output_dir, target_width)
                    except Exception as e:
                        print(f"[warn] Failed to process image in {notebook_rel_path}: {e}")
                        return None
    return None


def process_and_save_image(image_path, notebook_rel_path, output_dir, target_width):
    """Process and save an image from a file path."""
    with Image.open(image_path) as img:
        # Resize while preserving aspect ratio
        w_percent = target_width / float(img.size[0])
        h_size = int(float(img.size[1]) * w_percent)
        img = img.resize((target_width, h_size), Image.LANCZOS)

        # Save to unique file
        image_name = notebook_rel_path.replace("/", "_").replace(".ipynb", "_preview.png")
        output_path = os.path.join(output_dir, image_name)
        img.save(output_path)
        relpath = os.path.join("build/_assets/previews", image_name)
        return os.path.relpath(relpath, start=".").replace("\\", "/")


def process_and_save_image_from_bytes(image_bytes, notebook_rel_path, output_dir, target_width):
    """Process and save an image from bytes."""
    image = Image.open(BytesIO(image_bytes))
    # Resize while maintaining aspect ratio
    w_percent = target_width / float(image.size[0])
    h_size = int(float(image.size[1]) * w_percent)
    image = image.resize((target_width, h_size), Image.LANCZOS)

    # Create a filename based on notebook path
    base_name = notebook_rel_path.replace("/", "_").replace(".ipynb", "_preview.png")
    image_path = os.path.join(output_dir, base_name)
    image.save(image_path)
    relpath = os.path.join("build/_assets/previews", base_name)

    return os.path.relpath(relpath, start=".").replace("\\", "/")


def parse_gitmodules():
    """Parse .gitmodules to map paths to remote info."""
    gitmodules_path = os.path.join(ROOT_DIR, ".gitmodules")
    if not os.path.exists(gitmodules_path):
        return {}

    submodules = {}
    current = {}

    with open(gitmodules_path, "r") as f:
        for line in f:
            line = line.strip()
            if line.startswith("[submodule"):
                if current:
                    submodules[current["path"]] = current["url"]
                current = {}
            elif "=" in line:
                key, value = [x.strip() for x in line.split("=", 1)]
                current[key] = value
        if current:
            submodules[current["path"]] = current["url"]

    # Convert to path → { org, repo }
    result = {}
    for path, url in submodules.items():
        if url.endswith(".git"):
            url = url[:-4]
        if url.startswith("git@"):
            url = url.replace(":", "/").replace("git@", "https://")
        parsed = urlparse(url)
        parts = parsed.path.strip("/").split("/")
        if len(parts) >= 2:
            norm_path = os.path.normpath(path)
            result[norm_path] = {
                "org": parts[0],
                "repo": parts[1],
                "url": url
            }

    return result


def get_git_remote_info(repo_path):
    """Get git remote information for a repository."""
    try:
        print(repo_path)
        url = subprocess.check_output(
            ["git", "-C", repo_path, "config", "--get", "remote.origin.url"],
            text=True
        ).strip()
        print(url)
        if url.endswith(".git"):
            url = url[:-4]
        if url.startswith("git@"):
            url = url.replace(":", "/").replace("git@", "https://")
        parsed = urlparse(url)
        parts = parsed.path.strip("/").split("/")
        if len(parts) >= 2:
            return {"org": parts[0], "repo": parts[1], "url": url}
    except Exception as e:
        print(f"[warn] Could not get git remote info from {repo_path}: {e}")
    return {"org": DEF_ORG, "repo": DEF_REPO, "url": url}


def extract_notebook_metadata(notebook_path):
    """
    Extract metadata from notebook with priority system.
    Priority for all fields (title, description, image, tags, etc.):
    1. notebook.metadata.tags.{field} (HIGHEST PRIORITY)
    2. YAML frontmatter in first markdown cell
    3. Additional fallbacks handled elsewhere (e.g., title from first header, image from cells)
    
    Note: For images, we extract the URL/path but don't process it here.
    Image processing happens in extract_image_with_fallback().
    """
    try:
        nb = nbformat.read(notebook_path, as_version=4)
        
        # Check notebook metadata.tags first
        nb_metadata = nb.get('metadata', {})
        tags_metadata = nb_metadata.get('tags', {})
        
        # Try to extract from first cell YAML frontmatter
        frontmatter = {}
        if nb.cells and nb.cells[0].cell_type == 'markdown':
            content = nb.cells[0].source
            if content.strip().startswith('---'):
                block = content.split('---')[1]
                frontmatter = yaml.safe_load(block)
        
        # Merge metadata: tags_metadata takes precedence over frontmatter
        # First add frontmatter data
        merged = frontmatter.copy()
        
        # Then override with tags_metadata (this gives tags_metadata priority)
        # Note: We include 'image' here so we know it exists in metadata, but we'll
        # process it separately in extract_image_with_fallback()
        for key in ['title', 'description', 'domain', 'subtheme', 'service', 'platform', 'sensor', 'tags', 'image']:
            if key in tags_metadata:
                merged[key] = tags_metadata[key]
        
        return merged, nb
    except Exception as e:
        print(f"[warn] Failed to extract metadata from {notebook_path}: {e}")
    return {}, None


def myst_url_sanitation(url):
    """Reverse engineering the MyST URL sanitation."""
    clean_url = url.replace("_-_","-").replace("_", "-").replace(" ", "-").replace("..", "").replace(":", "").replace("'", "").replace('"', "").lower()
    parts = clean_url.split("/")
    cut_url = "/".join(parts[0:-1] + [parts[-1][:50]])
    return cut_url


def extract_title_from_first_header(nb):
    """
    Extract title from first # header in notebook cells.
    This is used as a fallback when title is not found in metadata or frontmatter.
    """
    for cell in nb.cells:
        if cell.cell_type == "markdown":
            lines = cell.source.splitlines()
            for line in lines:
                match = re.match(r'^\s*#\s+(.*)', line)
                if match:
                    return match.group(1).strip()
    return None


def collect_notebooks():
    """Collect all notebooks from local and submodule directories."""
    catalog = []
    git_url = get_git_remote_info(ROOT_DIR)["url"]
    submodules = parse_gitmodules()

    # --- Local notebooks
    local_path = os.path.join(ROOT_DIR, NOTEBOOK_DIR)
    for dirpath, _, filenames in os.walk(local_path):
        if any(ignored in dirpath for ignored in IGNORE_FOLDERS):
            continue
        for file in filenames:
            if file.endswith(".ipynb"):
                abs_path = os.path.join(dirpath, file)
                rel_path = os.path.relpath(abs_path, ROOT_DIR).replace("\\", "/")
                meta, nb = extract_notebook_metadata(abs_path)
                if nb is None:
                    nb = nbformat.read(abs_path, as_version=4)
                # Always call extract_image_with_fallback to ensure processing
                image = extract_image_with_fallback(nb, rel_path)
                
                catalog.append({
                    "title": meta.get("title", extract_title_from_first_header(nb) or os.path.splitext(file)[0].replace("_", " ")),
                    "description": meta.get("description", ""),
                    "metadata": meta,
                    "image": image,
                    "link": myst_url_sanitation(rel_path.replace(".ipynb", "")),
                    "org": DEF_ORG,
                    "repo": DEF_REPO,
                    "source": "local",
                    "path": rel_path,
                    "gitpuller": f"https://{JHUB_INSTANCE}/hub/user-redirect/git-pull?repo={git_url}&urlpath=lab/tree/{rel_path}&branch=main",
                })

    # --- Submodule notebooks
    submodules_root = os.path.join(ROOT_DIR, SUBMODULE_ROOT)
    if os.path.exists(submodules_root):
        for group in os.listdir(submodules_root):
            group_path = os.path.join(submodules_root, group)
            if not os.path.isdir(group_path):
                continue

            for repo in os.listdir(group_path):
                sub_path = os.path.join(group_path, repo)
                if not os.path.isdir(sub_path):
                    continue

                sub_rel = os.path.relpath(sub_path, ROOT_DIR)
                git_info = submodules.get(os.path.normpath(sub_rel), {"org": None, "repo": None})
                git_url = git_info.get("url", "")

                for dirpath, _, filenames in os.walk(sub_path):
                    for file in filenames:
                        if file.endswith(".ipynb"):
                            abs_path = os.path.join(dirpath, file)
                            rel_path = os.path.relpath(abs_path, ROOT_DIR).replace("\\", "/")
                            p = pathlib.Path(rel_path)
                            repo_path = pathlib.Path(*p.parts[2:])
                            meta, nb = extract_notebook_metadata(abs_path)
                            if nb is None:
                                nb = nbformat.read(abs_path, as_version=4)
                            # Always call extract_image_with_fallback to ensure processing
                            image = extract_image_with_fallback(nb, rel_path)
                            
                            catalog.append({
                                "title": meta.get("title", extract_title_from_first_header(nb) or os.path.splitext(file)[0].replace("_", " ")),
                                "description": meta.get("description", ""),
                                "metadata": meta,
                                "image": image,
                                "link": myst_url_sanitation(rel_path.replace(".ipynb", "")),
                                "org": git_info.get("org"),
                                "repo": git_info.get("repo"),
                                "source": "submodule",
                                "path": rel_path,
                                "gitpuller": f"https://{JHUB_INSTANCE}/hub/user-redirect/git-pull?repo={git_url}&urlpath=lab/tree/{repo_path}&branch=main",
                            })

    return catalog


if __name__ == "__main__":
    notebooks = collect_notebooks()
    with open(OUTPUT_FILE, "w") as f:
        json.dump(notebooks, f, indent=2)
    print(f"✅ Catalog saved to {OUTPUT_FILE} ({len(notebooks)} notebooks)")