#!/usr/bin/env python3

import json
from pathlib import Path
import requests
import subprocess
import sys

try:

    import markdownify

except ImportError:

    print(
        "Installing markdownify..."
    )

    subprocess.check_call(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "markdownify"
        ]
    )

    import markdownify

print("markdownify ready")


##############################################################################
# CONFIGURATION
##############################################################################

CANVAS_URL = "https://learn.tafe.wa.edu.au"
COURSE_ID = 2477
ACCESS_TOKEN = "YOUR_TOKEN"

OUTPUT_DIR = Path("./canvas_export")

##############################################################################
# HEADERS
##############################################################################

HEADERS = {
    "Authorization": f"Bearer {ACCESS_TOKEN}"
}

##############################################################################
# CANVAS API
##############################################################################

def canvas_get(endpoint):

    url = f"{CANVAS_URL}{endpoint}"

    r = requests.get(
        url,
        headers=HEADERS
    )

    r.raise_for_status()

    return r.json()

##############################################################################
# MODULES
##############################################################################

def get_modules():

    return canvas_get(
        f"/api/v1/courses/{COURSE_ID}/modules?per_page=100"
    )

##############################################################################
# MODULE ITEMS
##############################################################################

def get_module_items(module_id):

    return canvas_get(
        f"/api/v1/courses/{COURSE_ID}/modules/{module_id}/items?per_page=100"
    )

##############################################################################
# PAGE
##############################################################################

def get_page(page_url):

    return canvas_get(
        f"/api/v1/courses/{COURSE_ID}/pages/{page_url}"
    )

##############################################################################
# ASSIGNMENTS
##############################################################################

def get_assignments():

    return canvas_get(
        f"/api/v1/courses/{COURSE_ID}/assignments?per_page=100"
    )

##############################################################################
# EXPORT PAGES
##############################################################################

def export_module(module):

    module_name = module["name"]

    safe_name = (
        module_name
        .replace("/", "-")
        .replace(":", "-")
    )

    output_file = (
        OUTPUT_DIR /
        f"{safe_name}.md"
    )

    lines = []

    lines.append(
        "<!-- CANVAS_MODULE_START -->"
    )

    lines.append(
        f"MODULE_TITLE: {module_name}"
    )

    lines.append(
        f"MODULE_POSITION: {module['position']}"
    )

    lines.append(
        "<!-- CANVAS_MODULE_END -->"
    )

    lines.append("")

    module_items = get_module_items(
        module["id"]
    )

    page_order = 1

    for item in module_items:

        #
        # Pages
        #
        if item["type"] == "Page":

            page = get_page(
                item["page_url"]
            )

            md_content = markdownify(
                page["body"]
            )

            lines.append(
                "<!-- CANVAS_PAGE_START -->"
            )

            lines.append(
                f"PAGE_TITLE: {page['title']}"
            )

            lines.append(
                f"PAGE_SLUG: {item['page_url']}"
            )

            lines.append(
                "PAGE_TYPE: PAGE"
            )

            lines.append(
                f"PAGE_ORDER: {page_order}"
            )

            lines.append(
                "<!-- CANVAS_PAGE_END -->"
            )

            lines.append("")
            lines.append(md_content)
            lines.append("")

            page_order += 1

        #
        # Assignments
        #
        elif item["type"] == "Assignment":

            lines.append(
                "<!-- CANVAS_PAGE_START -->"
            )

            lines.append(
                f"PAGE_TITLE: {item['title']}"
            )

            lines.append(
                f"PAGE_SLUG: assignment-{item['id']}"
            )

            lines.append(
                "PAGE_TYPE: ASSIGNMENT"
            )

            lines.append(
                f"PAGE_ORDER: {page_order}"
            )

            lines.append(
                "<!-- CANVAS_PAGE_END -->"
            )

            lines.append("")

            lines.append(
                f"# {item['title']}"
            )

            lines.append("")
            lines.append(
                "*Canvas assignment content exported separately.*"
            )

            lines.append("")

            page_order += 1

    output_file.write_text(
        "\n".join(lines),
        encoding="utf-8"
    )

    print(
        f"Exported {output_file.name}"
    )

##############################################################################
# EXPORT COURSE METADATA
##############################################################################

def export_course_json():

    data = {
        "canvasUrl": CANVAS_URL,
        "courseId": COURSE_ID
    }

    (
        OUTPUT_DIR /
        "course.json"
    ).write_text(
        json.dumps(
            data,
            indent=4
        ),
        encoding="utf-8"
    )

##############################################################################
# MAIN
##############################################################################

def main():

    OUTPUT_DIR.mkdir(
        exist_ok=True
    )

    export_course_json()

    modules = get_modules()

    for module in modules:

        export_module(
            module
        )

    print(
        "\nExport completed."
    )

if __name__ == "__main__":
    main()