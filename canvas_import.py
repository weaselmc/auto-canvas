#!/usr/bin/env python3

import argparse
import mimetypes
import os
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from uuid import uuid4
from xml.etree.ElementTree import Element

import markdown
from markdown.inlinepatterns import InlineProcessor
import requests

##############################################################################
# CONFIGURATION
#
# Nothing course-specific is hard-coded here: the course ID, access token,
# Canvas URL and pages directory are all supplied at runtime via
# --course-id / --access-token / --canvas-url / --pages-dir (or the
# CANVAS_COURSE_ID / CANVAS_ACCESS_TOKEN / CANVAS_URL / CANVAS_PAGES_DIR
# environment variables). include.py is used only as a token fallback.
##############################################################################

CANVAS_URL = "https://learn.tafe.wa.edu.au"
DEFAULT_ASSIGNMENT_GROUP_WEIGHT = 0
DEFAULT_PAGES_DIR = Path(__file__).resolve().parent / "Pages"
DEFAULT_MEDIA_FOLDER = "Course Images"

try:
    from include import ACCESS_TOKEN as FALLBACK_TOKEN
except Exception:
    FALLBACK_TOKEN = None

COURSE_ID = None
PAGES_DIR = DEFAULT_PAGES_DIR
MEDIA_FOLDER = DEFAULT_MEDIA_FOLDER
MEDIA_URLS = {}

##############################################################################
# API HEADERS (rebuilt from the token in parse_args)
##############################################################################

HEADERS = {
    "Authorization": f"Bearer {FALLBACK_TOKEN or ''}"
}

##############################################################################
# MEDIA LINKING (media/ child of the pages directory)
##############################################################################

MEDIA_SRC_RE = re.compile(
    r'src=["\']media/([^"\']+?)["\']'
)


def link_media(html):

    def replace(match):

        rel = match.group(1).replace("\\", "/").strip()
        url = MEDIA_URLS.get(rel)
        if url is None:
            url = MEDIA_URLS.get(rel.split("/")[-1])
        if url is None:
            print(
                f"    WARNING: no uploaded file for "
                f"media reference {rel}"
            )
            return match.group(0)

        return f'src="{url}"'

    return MEDIA_SRC_RE.sub(replace, html)


##############################################################################
# MARKDOWN → CANVAS HTML
##############################################################################

class BareURLProcessor(InlineProcessor):

    def handleMatch(self, match, data):

        url = match.group(0)
        while url and url[-1] in ".,;:!?":
            url = url[:-1]

        for closing, opening in ((")", "("), ("]", "["), ("}", "{")):
            while (
                url.endswith(closing)
                and url.count(closing) > url.count(opening)
            ):
                url = url[:-1]

        if not url:
            return None, None, None

        link = Element("a", href=url)
        link.text = url

        return link, match.start(0), match.start(0) + len(url)


def markdown_to_canvas_html(md_content):

    parser = markdown.Markdown(
        extensions=[
            "tables",
            "fenced_code",
            "nl2br"
        ]
    )
    parser.inlinePatterns.register(
        BareURLProcessor(r"https?://[^\s<>]+"),
        "bare_url",
        50
    )
    html = parser.convert(md_content)

    html = link_media(
        html
    )

    return f"""
<div style="max-width:1200px; margin:auto;">

{html}

</div>
"""


class HTMLTextParser(HTMLParser):

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def normalize_html_text(value):

    parser = HTMLTextParser()
    parser.feed(value or "")

    return " ".join(
        "".join(parser.parts).split()
    )


##############################################################################
# MODULE MANAGEMENT
##############################################################################

def get_modules():

    url = (
        f"{CANVAS_URL}/api/v1/courses/"
        f"{COURSE_ID}/modules"
    )
    modules = []
    first_request = True

    while url:

        r = requests.get(
            url,
            headers=HEADERS,
            params={"per_page": 100} if first_request else None
        )

        r.raise_for_status()
        modules.extend(r.json())
        url = r.links.get("next", {}).get("url")
        first_request = False

    return modules


def ensure_assignment_group_id(group_name, group_weight=0):

    url = (
        f"{CANVAS_URL}/api/v1/courses/"
        f"{COURSE_ID}/assignment_groups"
    )
    groups = []
    first_request = True

    while url:

        r = requests.get(
            url,
            headers=HEADERS,
            params={"per_page": 100} if first_request else None
        )

        r.raise_for_status()
        groups.extend(r.json())
        url = r.links.get("next", {}).get("url")
        first_request = False

    normalized_name = group_name.strip().casefold()
    matches = [
        group
        for group in groups
        if group.get("name", "").strip().casefold() == normalized_name
    ]

    if len(matches) > 1:
        raise ValueError(
            f"Multiple Canvas assignment groups are named "
            f"'{group_name}'."
        )

    if matches:
        return matches[0]["id"]

    r = requests.post(
        f"{CANVAS_URL}/api/v1/courses/{COURSE_ID}/assignment_groups",
        headers=HEADERS,
        json={
            "assignment_group": {
                "name": group_name,
                "group_weight": group_weight
            }
        }
    )
    r.raise_for_status()
    assignment_group = r.json()

    if "id" not in assignment_group:
        raise ValueError(
            f"Canvas created assignment group '{group_name}' without "
            "returning its ID."
        )

    return assignment_group["id"]


def find_module(module_title):

    modules = get_modules()
    normalized_title = module_title.strip().casefold()

    for module in modules:

        normalized_name = module["name"].strip().casefold()

        if (
            normalized_name == normalized_title
            or (
                normalized_name.startswith("[")
                and normalized_name.rsplit("] ", 1)[-1] == normalized_title
            )
        ):

            return module

    return None


def create_module(
    module_title,
    position
):

    url = (
        f"{CANVAS_URL}/api/v1/courses/"
        f"{COURSE_ID}/modules"
    )

    payload = {
        "module": {
            "name": module_title,
            "position": position,
            "published": True
        }
    }

    r = requests.post(
        url,
        headers=HEADERS,
        json=payload
    )

    r.raise_for_status()

    return r.json()["id"]


def update_module(
    module_id,
    module_title,
    position
):

    url = (
        f"{CANVAS_URL}/api/v1/courses/"
        f"{COURSE_ID}/modules/{module_id}"
    )

    payload = {
        "module": {
            "name": module_title,
            "position": position,
            "published": True
        }
    }

    r = requests.put(
        url,
        headers=HEADERS,
        json=payload
    )

    r.raise_for_status()

    return r.json()["id"]


def get_module_items(module_id):

    url = (
        f"{CANVAS_URL}/api/v1/courses/"
        f"{COURSE_ID}/modules/{module_id}/items"
    )
    items = []
    first_request = True

    while url:

        r = requests.get(
            url,
            headers=HEADERS,
            params={"per_page": 100} if first_request else None
        )

        r.raise_for_status()
        items.extend(r.json())
        url = r.links.get("next", {}).get("url")
        first_request = False

    return items


def update_module_item(
    module_id,
    item_id,
    position,
    completion_requirement=None
):

    url = (
        f"{CANVAS_URL}/api/v1/courses/"
        f"{COURSE_ID}/modules/"
        f"{module_id}/items/{item_id}"
    )

    payload = {
        "module_item": {
            "position": position
        }
    }
    if completion_requirement is not None:
        payload["module_item"]["completion_requirement"] = (
            completion_requirement
        )

    r = requests.put(
        url,
        headers=HEADERS,
        json=payload
    )

    r.raise_for_status()


def delete_module_item(module_id, item_id):

    url = (
        f"{CANVAS_URL}/api/v1/courses/"
        f"{COURSE_ID}/modules/"
        f"{module_id}/items/{item_id}"
    )

    r = requests.delete(
        url,
        headers=HEADERS
    )

    if r.status_code not in [200, 204]:
        r.raise_for_status()


##############################################################################
# MEDIA UPLOAD (course files API)
##############################################################################

def list_course_files(course_id):

    url = f"{CANVAS_URL}/api/v1/courses/{course_id}/files"
    files = []

    while url:

        r = requests.get(
            url,
            headers=HEADERS,
            params={"per_page": 50}
        )
        r.raise_for_status()
        files.extend(r.json())
        url = r.links.get("next", {}).get("url")

    return files


def resolve_file_url(data):

    url = data.get("url") or data.get("download_url")
    if url and not url.startswith("http"):
        url = CANVAS_URL + url

    return url


def upload_course_file(course_id, local_path, folder_name):

    mime = (
        mimetypes.guess_type(local_path.name)[0]
        or "application/octet-stream"
    )

    url = f"{CANVAS_URL}/api/v1/courses/{course_id}/files"

    with open(local_path, "rb") as f:
        r = requests.post(
            url,
            headers=HEADERS,
            files={"file": (local_path.name, f, mime)},
            data={
                "name": local_path.name,
                "folder_name": folder_name
            }
        )

    r.raise_for_status()
    data = r.json()

    if data.get("upload_url"):
        # Instructure/inscloudgate two-step upload: send the bytes to
        # the presigned URL as a plain multipart "file" part, then
        # embed via the stable /files/<id>?download=false pattern
        # (signed URLs in the response expire, so do not use them).
        with open(local_path, "rb") as f:
            up = requests.post(
                data["upload_url"],
                files={"file": (local_path.name, f, mime)},
                timeout=300
            )
        up.raise_for_status()
        up_data = up.json()
        file_id = up_data.get("id")
        if not file_id:
            raise RuntimeError(
                f"Step 2 upload of {local_path.name} "
                f"did not return a file id"
            )
        return (
            f"{CANVAS_URL}/files/{file_id}?download=false"
        )

    file_id = data.get("id")
    if file_id:
        return (
            f"{CANVAS_URL}/files/{file_id}?download=false"
        )

    return resolve_file_url(
        data
    )


def upload_media(course_id, pages_dir, folder_name):

    global MEDIA_URLS
    MEDIA_URLS = {}

    media_dir = Path(pages_dir) / "media"
    if not media_dir.is_dir():
        return []

    local_files = sorted(
        p for p in media_dir.rglob("*") if p.is_file()
    )
    if not local_files:
        return []

    existing = {
        (f.get("display_name") or f.get("filename"), f.get("size")): f
        for f in list_course_files(
            course_id
        )
    }

    uploaded = []
    for path in local_files:

        rel = path.relative_to(media_dir).as_posix()
        hit = existing.get((path.name, path.stat().st_size))
        if hit:
            if hit.get("id"):
                file_url = (
                    f"{CANVAS_URL}/files/"
                    f"{hit['id']}?download=false"
                )
            else:
                file_url = resolve_file_url(
                    hit
                )
            print(
                f"    Reusing existing course file {path.name}"
            )
        else:
            file_url = upload_course_file(
                course_id,
                path,
                folder_name
            )
            print(
                f"    Uploaded {path.name}"
            )

        if not file_url:
            raise RuntimeError(
                f"Canvas did not return a URL for {path.name}"
            )

        MEDIA_URLS[rel] = file_url
        MEDIA_URLS[path.name] = file_url
        uploaded.append(rel)

    return uploaded


##############################################################################
# PAGE CREATION
##############################################################################

def create_page(title, content):

    html_content = markdown_to_canvas_html(
        content
    )

    url = (
        f"{CANVAS_URL}/api/v1/courses/"
        f"{COURSE_ID}/pages"
    )

    payload = {
        "wiki_page": {
            "title": title,
            "body": html_content,
            "published": True
        }
    }

    r = requests.post(
        url,
        headers=HEADERS,
        json=payload
    )

    r.raise_for_status()

    return r.json()["url"]


def get_page(page_url):

    url = (
        f"{CANVAS_URL}/api/v1/courses/"
        f"{COURSE_ID}/pages/{page_url}"
    )

    r = requests.get(
        url,
        headers=HEADERS
    )

    r.raise_for_status()

    return r.json()


def update_page(page_url, html_content):

    url = (
        f"{CANVAS_URL}/api/v1/courses/"
        f"{COURSE_ID}/pages/{page_url}"
    )

    payload = {
        "wiki_page": {
            "body": html_content
        }
    }

    r = requests.put(
        url,
        headers=HEADERS,
        json=payload
    )

    r.raise_for_status()


def set_page_published(page_url, published):

    url = (
        f"{CANVAS_URL}/api/v1/courses/"
        f"{COURSE_ID}/pages/{page_url}"
    )

    r = requests.put(
        url,
        headers=HEADERS,
        json={"wiki_page": {"published": published}}
    )

    r.raise_for_status()


def get_quizzes():

    url = (
        f"{CANVAS_URL}/api/quiz/v1/courses/"
        f"{COURSE_ID}/quizzes"
    )
    quizzes = []

    while url:

        r = requests.get(
            url,
            headers=HEADERS
        )

        r.raise_for_status()
        quizzes.extend(r.json())
        url = r.links.get("next", {}).get("url")

    return quizzes


def find_quiz(quiz_title, quizzes):

    normalized_title = quiz_title.strip().casefold()
    matches = [
        quiz
        for quiz in quizzes
        if quiz.get("title", "").strip().casefold() == normalized_title
    ]

    if len(matches) > 1:
        raise ValueError(
            f"Multiple New Quizzes have the title '{quiz_title}'."
        )

    return matches[0] if matches else None


def get_quiz(quiz_id):

    url = (
        f"{CANVAS_URL}/api/quiz/v1/courses/"
        f"{COURSE_ID}/quizzes/{quiz_id}"
    )

    r = requests.get(
        url,
        headers=HEADERS
    )

    r.raise_for_status()

    return r.json()


def get_quiz_items(quiz_id):

    url = (
        f"{CANVAS_URL}/api/quiz/v1/courses/"
        f"{COURSE_ID}/quizzes/{quiz_id}/items"
    )
    items = []

    while url:

        r = requests.get(
            url,
            headers=HEADERS
        )

        r.raise_for_status()
        items.extend(r.json())
        url = r.links.get("next", {}).get("url")

    return items


def create_new_quiz(quiz_payload):

    url = (
        f"{CANVAS_URL}/api/quiz/v1/courses/"
        f"{COURSE_ID}/quizzes"
    )

    r = requests.post(
        url,
        headers=HEADERS,
        json={"quiz": quiz_payload}
    )

    r.raise_for_status()

    return r.json()


def update_new_quiz(quiz_id, quiz_payload):

    url = (
        f"{CANVAS_URL}/api/quiz/v1/courses/"
        f"{COURSE_ID}/quizzes/{quiz_id}"
    )

    r = requests.patch(
        url,
        headers=HEADERS,
        json={"quiz": quiz_payload}
    )

    r.raise_for_status()

    return r.json()


def get_assignment(assignment_id):

    url = (
        f"{CANVAS_URL}/api/v1/courses/"
        f"{COURSE_ID}/assignments/{assignment_id}"
    )

    r = requests.get(
        url,
        headers=HEADERS
    )

    r.raise_for_status()

    return r.json()


def set_assignment_published(assignment_id, published):

    url = (
        f"{CANVAS_URL}/api/v1/courses/"
        f"{COURSE_ID}/assignments/{assignment_id}"
    )

    r = requests.put(
        url,
        headers=HEADERS,
        json={"assignment": {"published": published}}
    )

    r.raise_for_status()


def get_assignments():

    url = (
        f"{CANVAS_URL}/api/v1/courses/"
        f"{COURSE_ID}/assignments"
    )
    assignments = []
    first_request = True

    while url:

        r = requests.get(
            url,
            headers=HEADERS,
            params={"per_page": 100} if first_request else None
        )

        r.raise_for_status()
        assignments.extend(r.json())
        url = r.links.get("next", {}).get("url")
        first_request = False

    return assignments


def find_canvas_assignment(title, assignments):

    normalized_title = title.strip().casefold()
    matches = [
        assignment
        for assignment in assignments
        if assignment.get("name", "").strip().casefold()
        == normalized_title
    ]

    if len(matches) > 1:
        raise ValueError(
            f"Multiple Canvas assignments have the title '{title}'."
        )

    return matches[0] if matches else None


def build_assignment_payload(page, assignment_group_id):

    return {
        "name": page["title"],
        "description": markdown_to_canvas_html(page["content"]),
        "points_possible": page["points"] or 0,
        "grading_type": "points",
        "submission_types": ["online_upload"],
        "allowed_attempts": 2,
        "assignment_group_id": assignment_group_id,
        "published": True
    }


def assignment_matches_expected(current, expected):

    return (
        current.get("name") == expected["name"]
        and normalize_html_text(current.get("description", ""))
        == normalize_html_text(expected["description"])
        and float(current.get("points_possible") or 0)
        == float(expected["points_possible"])
        and current.get("grading_type") == expected["grading_type"]
        and current.get("submission_types") == expected["submission_types"]
        and current.get("allowed_attempts") == expected["allowed_attempts"]
        and str(current.get("assignment_group_id"))
        == str(expected["assignment_group_id"])
        and current.get("published") is True
    )


def validate_canvas_assignment(assignment, expected):

    if not assignment_matches_expected(assignment, expected):
        raise RuntimeError(
            f"Canvas did not apply the required settings for assignment "
            f"'{expected['name']}'."
        )


def create_canvas_assignment(payload):

    url = (
        f"{CANVAS_URL}/api/v1/courses/"
        f"{COURSE_ID}/assignments"
    )

    r = requests.post(
        url,
        headers=HEADERS,
        json={"assignment": payload}
    )

    r.raise_for_status()

    return r.json()


def update_canvas_assignment(assignment_id, payload):

    url = (
        f"{CANVAS_URL}/api/v1/courses/"
        f"{COURSE_ID}/assignments/{assignment_id}"
    )

    r = requests.put(
        url,
        headers=HEADERS,
        json={"assignment": payload}
    )

    r.raise_for_status()

    return r.json()


def sync_canvas_assignment(page, assignments, assignment_group_id):

    payload = build_assignment_payload(
        page,
        assignment_group_id
    )
    existing = find_canvas_assignment(
        page["title"],
        assignments
    )

    if existing is None:
        assignment = create_canvas_assignment(payload)
        validate_canvas_assignment(
            assignment,
            payload
        )
        assignments.append(assignment)
        return assignment

    if assignment_matches_expected(existing, payload):
        print(
            f"    No changes for assignment "
            f"{page['title']}"
        )
        return existing

    print(
        f"    Updating existing assignment "
        f"{page['title']}"
    )
    assignment = update_canvas_assignment(
        existing["id"],
        payload
    )
    validate_canvas_assignment(
        assignment,
        payload
    )
    assignments[assignments.index(existing)] = assignment

    return assignment


def build_new_quiz_payload(
    page,
    instructions,
    question_count,
    assignment_group_id
):

    points_possible = page["points"] or question_count
    if points_possible <= 0:
        raise ValueError(
            f"Quiz '{page['title']}' must have positive points."
        )

    if page["required_percent"] is not None:
        instructions = (
            f"{instructions}\n\n"
            f"Completion requirement: "
            f"{page['required_percent']}% correct."
        )

    return {
        "title": page["title"],
        "instructions": markdown_to_canvas_html(instructions),
        "points_possible": points_possible,
        "grading_type": "points",
        "assignment_group_id": assignment_group_id,
        "quiz_settings": {
            "shuffle_answers": True,
            "shuffle_questions": True,
            "one_at_a_time_type": "question",
            "allow_backtracking": True,
            "multiple_attempts": {
                "multiple_attempts_enabled": True,
                "attempt_limit": True,
                "max_attempts": 2,
                "score_to_keep": "highest"
            },
            "result_view_settings": {
                "result_view_restricted": True,
                "display_points_awarded": True,
                "display_points_possible": True,
                "display_items": True,
                "display_item_response": True,
                "display_item_response_qualifier": "always",
                "display_item_response_correctness": True,
                "display_item_response_correctness_qualifier": "always",
                "display_item_correct_answer": False,
                "display_item_feedback": False
            }
        }
    }


def get_quiz_completion_requirement(page):

    required_percent = page["required_percent"]
    if required_percent is None:
        return None

    if not 0 <= required_percent <= 100 or page["points"] is None:
        raise ValueError(
            f"Quiz '{page['title']}' needs REQUIRED_PERCENT between 0 and 100 "
            "and a POINTS value."
        )

    minimum_score = page["points"] * required_percent / 100
    if minimum_score.is_integer():
        minimum_score = int(minimum_score)

    return {
        "type": "min_score",
        "min_score": minimum_score
    }


def settings_contain_expected(current, expected):

    if not isinstance(current, dict):
        return False

    for key, value in expected.items():
        if isinstance(value, dict):
            if not settings_contain_expected(current.get(key), value):
                return False
        elif current.get(key) != value:
            return False

    return True


def quiz_matches_expected(
    current,
    expected,
    include_assignment_group=True
):

    return (
        current.get("title") == expected["title"]
        and normalize_html_text(current.get("instructions", ""))
        == normalize_html_text(expected["instructions"])
        and float(current.get("points_possible") or 0)
        == float(expected["points_possible"])
        and current.get("grading_type") == expected["grading_type"]
        and (
            not include_assignment_group
            or str(current.get("assignment_group_id"))
            == str(expected["assignment_group_id"])
        )
        and settings_contain_expected(
            current.get("quiz_settings"),
            expected["quiz_settings"]
        )
    )


def quiz_questions_match(existing_items, questions, points_possible):

    existing_items = sorted(
        existing_items,
        key=lambda item: item.get("position", 0)
    )

    if len(existing_items) != len(questions):
        return False

    question_points = points_possible / len(questions)

    for position, (item, question) in enumerate(
        zip(existing_items, questions),
        start=1
    ):
        entry = item.get("entry") or {}
        interaction_data = entry.get("interaction_data") or {}
        existing_choices = sorted(
            interaction_data.get("choices", []),
            key=lambda choice: choice.get("position", 0)
        )

        if (
            item.get("position") != position
            or abs(float(item.get("points_possible") or 0) - question_points) > 1e-8
            or entry.get("interaction_type_slug") != "choice"
            or normalize_html_text(entry.get("item_body", ""))
            != normalize_html_text(
                markdown_to_canvas_html(question["stem"])
            )
            or len(existing_choices) != len(question["choices"])
        ):
            return False

        for existing_choice, expected_choice in zip(
            existing_choices,
            question["choices"]
        ):
            if normalize_html_text(existing_choice.get("itemBody", "")) != (
                normalize_html_text(
                    markdown_to_canvas_html(expected_choice["text"])
                )
            ):
                return False

        correct_choice_id = (
            (entry.get("scoring_data") or {}).get("value")
        )
        correct_choice = next(
            (
                choice
                for choice in existing_choices
                if choice.get("id") == correct_choice_id
            ),
            None
        )

        if (
            correct_choice is None
            or normalize_html_text(correct_choice.get("itemBody", ""))
            != normalize_html_text(
                markdown_to_canvas_html(
                    next(
                        choice["text"]
                        for choice in question["choices"]
                        if choice["label"] == question["answer"]
                    )
                )
            )
        ):
            return False

    return True


def sync_new_quiz(
    page,
    assignment_group_id,
    assignment_group_name,
    quizzes=None
):

    instructions, questions = parse_quiz_content(
        page["content"],
        page["title"]
    )
    points_possible = page["points"] or len(questions)
    quiz_payload = build_new_quiz_payload(
        page,
        instructions,
        len(questions),
        assignment_group_id
    )
    if quizzes is None:
        quizzes = get_quizzes()

    existing = find_quiz(page["title"], quizzes)

    if existing is None:
        quiz = create_new_quiz(quiz_payload)
        if not quiz_matches_expected(quiz, quiz_payload):
            raise RuntimeError(
                f"Canvas did not apply the required settings for quiz "
                f"'{page['title']}'."
            )
        quiz_id = quiz["id"]
        if quizzes is not None:
            quizzes.append(quiz)

        for position, question in enumerate(questions, start=1):
            create_new_quiz_item(
                quiz_id,
                question,
                position,
                points_possible / len(questions)
            )

        set_assignment_published(
            quiz_id,
            True
        )
        return quiz

    quiz_id = existing["id"]
    current_quiz = get_quiz(quiz_id)
    current_items = get_quiz_items(quiz_id)
    questions_match = quiz_questions_match(
        current_items,
        questions,
        points_possible
    )

    quiz_content_matches = quiz_matches_expected(
        current_quiz,
        quiz_payload,
        include_assignment_group=False
    )
    group_matches = (
        str(current_quiz.get("assignment_group_id"))
        == str(assignment_group_id)
    )

    if quiz_content_matches and questions_match:
        if not group_matches:
            print(
                f"    Moving quiz to assignment group "
                f"{assignment_group_name}: "
                f"{page['title']}"
            )
            current_quiz = update_new_quiz(
                quiz_id,
                {"assignment_group_id": assignment_group_id}
            )
            if (
                str(current_quiz.get("assignment_group_id"))
                != str(assignment_group_id)
            ):
                raise RuntimeError(
                    f"Canvas did not move quiz '{page['title']}' to "
                    f"assignment group '{assignment_group_name}'."
                )

        if not current_quiz.get("published"):
            set_assignment_published(
                quiz_id,
                True
            )
        if group_matches:
            print(
                f"    No changes for quiz "
                f"{page['title']}"
            )
        return current_quiz

    assignment = get_assignment(quiz_id)
    if assignment.get("has_submitted_submissions") is not False:
        raise RuntimeError(
            f"Quiz '{page['title']}' differs from Markdown, but its "
            "submission status is not confirmed empty. It was not changed."
        )

    print(
        f"    Updating existing New Quiz "
        f"{page['title']}"
    )
    set_assignment_published(
        quiz_id,
        False
    )
    updated_quiz = update_new_quiz(
        quiz_id,
        quiz_payload
    )
    if not quiz_matches_expected(updated_quiz, quiz_payload):
        raise RuntimeError(
            f"Canvas did not apply the required settings for quiz "
            f"'{page['title']}'."
        )

    if not questions_match:
        for item in current_items:
            delete_new_quiz_item(
                quiz_id,
                item["id"]
            )

        for position, question in enumerate(questions, start=1):
            create_new_quiz_item(
                quiz_id,
                question,
                position,
                points_possible / len(questions)
            )

    set_assignment_published(
        quiz_id,
        True
    )

    return get_quiz(quiz_id)


def create_new_quiz_item(quiz_id, question, position, points_possible):

    choice_ids = {
        choice["label"]: str(uuid4())
        for choice in question["choices"]
    }

    choices = [
        {
            "id": choice_ids[choice["label"]],
            "position": index,
            "itemBody": markdown_to_canvas_html(choice["text"])
        }
        for index, choice in enumerate(question["choices"], start=1)
    ]

    correct_choice_id = choice_ids[question["answer"]]
    payload = {
        "item": {
            "entry_type": "Item",
            "position": position,
            "points_possible": points_possible,
            "entry": {
                "title": f"Question {question['number']}",
                "item_body": markdown_to_canvas_html(question["stem"]),
                "interaction_type_slug": "choice",
                "interaction_data": {
                    "choices": choices
                },
                "properties": {
                    "shuffleRules": {
                        "choices": {
                            "toLock": [],
                            "shuffled": False
                        }
                    },
                    "varyPointsByAnswer": False
                },
                "scoring_data": {
                    "value": correct_choice_id
                },
                "scoring_algorithm": "Equivalence"
            }
        }
    }

    url = (
        f"{CANVAS_URL}/api/quiz/v1/courses/"
        f"{COURSE_ID}/quizzes/{quiz_id}/items"
    )

    r = requests.post(
        url,
        headers=HEADERS,
        json=payload
    )

    r.raise_for_status()

    return r.json()


def delete_new_quiz_item(quiz_id, item_id):

    url = (
        f"{CANVAS_URL}/api/quiz/v1/courses/"
        f"{COURSE_ID}/quizzes/{quiz_id}/items/{item_id}"
    )

    r = requests.delete(
        url,
        headers=HEADERS
    )

    r.raise_for_status()


def get_pages():

    url = (
        f"{CANVAS_URL}/api/v1/courses/"
        f"{COURSE_ID}/pages"
    )
    pages = []
    first_request = True

    while url:

        r = requests.get(
            url,
            headers=HEADERS,
            params={"per_page": 100} if first_request else None
        )

        r.raise_for_status()
        pages.extend(r.json())
        url = r.links.get("next", {}).get("url")
        first_request = False

    return pages


def find_page(page_slug, page_title, pages):

    normalized_slug = page_slug.strip().casefold()

    for page in pages:

        if page.get("url", "").strip().casefold() == normalized_slug:
            return page

    normalized_title = page_title.strip().casefold()

    for page in pages:

        if page.get("title", "").strip().casefold() == normalized_title:
            return page

    return None


def add_page_to_module(
    module_id,
    page_url,
    position
):

    url = (
        f"{CANVAS_URL}/api/v1/courses/"
        f"{COURSE_ID}/modules/"
        f"{module_id}/items"
    )

    payload = {
        "module_item": {
            "type": "Page",
            "page_url": page_url,
            "position": position,
            "published": True
        }
    }

    r = requests.post(
        url,
        headers=HEADERS,
        json=payload
    )

    r.raise_for_status()


def add_assignment_to_module(
    module_id,
    assignment_id,
    position,
    completion_requirement=None
):

    url = (
        f"{CANVAS_URL}/api/v1/courses/"
        f"{COURSE_ID}/modules/"
        f"{module_id}/items"
    )

    payload = {
        "module_item": {
            "type": "Assignment",
            "content_id": assignment_id,
            "position": position,
            "published": True,
            "completion_requirement": completion_requirement
        }
    }

    r = requests.post(
        url,
        headers=HEADERS,
        json=payload
    )

    if not r.ok:
        raise requests.HTTPError(
            f"Canvas rejected the assignment module item "
            f"({r.status_code}): {r.text}",
            response=r
        )

##############################################################################
# PARSE MODULE
##############################################################################

def parse_module(md_text):

    pattern = (
        r"<!-- CANVAS_MODULE_START -->(.*?)"
        r"<!-- CANVAS_MODULE_END -->"
    )

    match = re.search(
        pattern,
        md_text,
        re.S
    )

    if not match:
        raise ValueError(
            "Module definition missing."
        )

    block = match.group(1)

    title = re.search(
        r"MODULE_TITLE:\s*(.*)",
        block
    ).group(1).strip()

    code = re.search(
        r"MODULE_CODE:\s*(.*)",
        block
    ).group(1).strip()

    position = int(
        re.search(
            r"MODULE_POSITION:\s*(\d+)",
            block
        ).group(1)
    )
    assignment_group_match = re.search(
        r"ASSIGNMENT_GROUP:\s*(.*)",
        block
    )

    return {
        "title": title,
        "code": code,
        "position": position,
        "assignment_group": (
            assignment_group_match.group(1).strip()
            if assignment_group_match
            else ""
        )
    }

##############################################################################
# PARSE PAGES
##############################################################################

def parse_pages(md_text):

    pattern = (
        r"<!-- CANVAS_PAGE_START -->(.*?)"
        r"<!-- CANVAS_PAGE_END -->"
    )

    matches = list(
        re.finditer(
            pattern,
            md_text,
            re.S
        )
    )

    pages = []

    for i, match in enumerate(matches):

        metadata = match.group(1)

        start = match.end()

        if i + 1 < len(matches):
            end = matches[i + 1].start()
        else:
            end = len(md_text)

        content = md_text[
            start:end
        ].strip()

        def extract(field):

            m = re.search(
                rf"{field}:\s*(.*)",
                metadata
            )

            return (
                m.group(1).strip()
                if m
                else ""
            )

        pages.append(
            {
                "title":
                    extract("PAGE_TITLE"),
                "slug":
                    extract("PAGE_SLUG"),
                "type":
                    extract("PAGE_TYPE"),
                "points":
                    int(extract("POINTS"))
                    if extract("POINTS")
                    else None,
                "required_percent":
                    int(extract("REQUIRED_PERCENT"))
                    if extract("REQUIRED_PERCENT")
                    else None,
                "order":
                    int(
                        extract(
                            "PAGE_ORDER"
                        )
                    ),
                "content":
                    content
            }
        )

    pages.sort(
        key=lambda p: p["order"]
    )

    return pages


def parse_quiz_content(content, title):

    question_headers = list(
        re.finditer(
            r"(?m)^#{2,3}\s+Question\s+(\d+)\s*$",
            content
        )
    )
    stimulus_headers = list(
        re.finditer(
            r"(?m)^#{2,3}\s+Stimulus\s+([A-Za-z0-9_-]+)\s*$",
            content
        )
    )

    if not question_headers:
        raise ValueError(
            f"No questions found in quiz '{title}'."
        )

    content_headers = sorted(
        question_headers + stimulus_headers,
        key=lambda item: item.start()
    )
    stimuli = {}
    for stimulus_header in stimulus_headers:
        next_header = next(
            (
                item
                for item in content_headers
                if item.start() > stimulus_header.start()
            ),
            None
        )
        stimulus_end = (
            next_header.start()
            if next_header
            else len(content)
        )
        stimulus_id = stimulus_header.group(1)
        stimulus_content = content[
            stimulus_header.end():stimulus_end
        ].strip()
        use_instruction = re.compile(
            r"(?m)^\*\*Use Stimulus\s+"
            + re.escape(stimulus_id)
            + r"\s+to answer Questions \d+"
            + r"(?:\s*[–-]\s*\d+)?\.\*\*\s*$"
        )
        stimulus_content = use_instruction.sub(
            "",
            stimulus_content
        ).strip()

        if not stimulus_content:
            raise ValueError(
                f"Stimulus '{stimulus_id}' in quiz '{title}' is empty."
            )
        if stimulus_id in stimuli:
            raise ValueError(
                f"Quiz '{title}' contains duplicate stimulus "
                f"'{stimulus_id}'."
            )

        stimuli[stimulus_id] = stimulus_content

    instructions = content[:question_headers[0].start()].strip()
    questions = []

    for index, header in enumerate(question_headers):

        section_start = header.end()
        section_end = (
            question_headers[index + 1].start()
            if index + 1 < len(question_headers)
            else len(content)
        )
        section = content[section_start:section_end].strip()
        stimulus_id_matches = list(
            re.finditer(
                r"(?m)^STIMULUS_ID:\s*([A-Za-z0-9_-]+)\s*$",
                section
            )
        )
        if len(stimulus_id_matches) > 1:
            raise ValueError(
                f"Quiz '{title}' question {header.group(1)} "
                "has multiple STIMULUS_ID values."
            )
        stimulus_id = None
        if stimulus_id_matches:
            stimulus_id_match = stimulus_id_matches[0]
            stimulus_id = stimulus_id_match.group(1)
            section = (
                section[:stimulus_id_match.start()]
                + section[stimulus_id_match.end():]
            ).strip()

        answer_match = re.search(
            r"(?m)^\*\*Answer:\s*([A-Z])\s*\*\*\s*$",
            section
        )
        option_matches = list(
            re.finditer(
                r"(?m)^([A-Z])\.\s*(.+)$",
                section
            )
        )

        if not answer_match or len(option_matches) < 2:
            raise ValueError(
                f"Quiz '{title}' question {header.group(1)} "
                "must have answer choices and an '**Answer: X**' key."
            )

        labels = [match.group(1) for match in option_matches]
        if len(labels) != len(set(labels)):
            raise ValueError(
                f"Quiz '{title}' question {header.group(1)} "
                "has duplicate answer labels."
            )

        answer = answer_match.group(1)
        if answer not in labels:
            raise ValueError(
                f"Quiz '{title}' question {header.group(1)} "
                f"answer key '{answer}' does not match an option."
            )

        stem = section[:option_matches[0].start()].strip()
        if not stem:
            raise ValueError(
                f"Quiz '{title}' question {header.group(1)} "
                "has no question text."
            )
        if stimulus_id:
            if stimulus_id not in stimuli:
                raise ValueError(
                    f"Quiz '{title}' question {header.group(1)} "
                    f"references missing stimulus '{stimulus_id}'."
                )
            # New Quizzes cannot create stimulus items through its public API.
            stem = (
                f"### Stimulus {stimulus_id}\n\n"
                f"{stimuli[stimulus_id]}\n\n"
                "---\n\n"
                f"{stem}"
            )

        choices = []
        for choice_index, option_match in enumerate(option_matches):
            choice_end = (
                option_matches[choice_index + 1].start()
                if choice_index + 1 < len(option_matches)
                else answer_match.start()
            )
            choice_text = section[
                option_match.start(2):choice_end
            ].strip()

            if not choice_text:
                raise ValueError(
                    f"Quiz '{title}' question {header.group(1)} "
                    f"choice '{option_match.group(1)}' is empty."
                )

            choices.append(
                {
                    "label": option_match.group(1),
                    "text": choice_text
                }
            )

        questions.append(
            {
                "number": int(header.group(1)),
                "stem": stem,
                "choices": choices,
                "answer": answer
            }
        )

    return instructions, questions

##############################################################################
# FILE SELECTION
##############################################################################

def get_markdown_files(single_file=None):

    if single_file:

        file = Path(
            single_file
        )

        if not file.is_absolute():
            file = (
                PAGES_DIR / file
            )

        return [file]

    return sorted(
        (
            p for p in PAGES_DIR.iterdir()
            if p.is_file()
            and p.suffix.lower() == ".md"
            and p.name.lower().startswith("session")
        ),
        key=lambda p: p.name.lower()
    )

##############################################################################
# IMPORT
##############################################################################

def import_file(md_file):

    print(
        f"\nProcessing "
        f"{md_file.name}"
    )

    md_text = md_file.read_text(
        encoding="utf-8"
    )

    module = parse_module(
        md_text
    )
    pages = parse_pages(
        md_text
    )
    if not pages:
        raise ValueError(
            f"No pages found in {md_file.name}; "
            "refusing to remove module page items."
        )

    quiz_pages = [
        page
        for page in pages
        if page["type"].strip().upper() == "QUIZ"
    ]
    assignment_pages = [
        page
        for page in pages
        if page["type"].strip().upper() == "ASSIGNMENT"
    ]
    assignment_group_name = module["assignment_group"]
    assignment_group_id = None
    if quiz_pages or assignment_pages:
        if not assignment_group_name:
            raise ValueError(
                f"{md_file.name} contains quizzes or assignments but is "
                "missing ASSIGNMENT_GROUP in its module metadata."
            )
        assignment_group_id = ensure_assignment_group_id(
            assignment_group_name,
            DEFAULT_ASSIGNMENT_GROUP_WEIGHT
        )

    existing = find_module(
        module["title"]
    )

    if existing:
        module_id = existing["id"]
        print(
            f"Updating existing module "
            f"{module['code']}"
        )
        update_module(
            module_id,
            module["title"],
            module["position"]
        )
    else:
        module_id = create_module(
            module["title"],
            module["position"]
        )

    existing_pages = get_pages()
    module_items = get_module_items(module_id)
    desired_page_urls = set()
    desired_quiz_ids = set()
    quizzes = get_quizzes() if quiz_pages else []
    assignments = get_assignments() if assignment_pages else []

    for page in pages:

        print(
            f"  Publishing "
            f"{page['title']}"
        )

        if page["type"].strip().upper() == "QUIZ":
            quiz = sync_new_quiz(
                page,
                assignment_group_id,
                assignment_group_name,
                quizzes
            )
            quiz_id = quiz["id"]
            quiz_id_key = str(quiz_id)
            desired_quiz_ids.add(quiz_id_key)
            completion_requirement = get_quiz_completion_requirement(page)

            existing_item = next(
                (
                    item
                    for item in module_items
                    if item["type"] == "Assignment"
                    and str(item.get("content_id")) == quiz_id_key
                ),
                None
            )

            if existing_item:
                if (
                    existing_item["position"] != page["order"]
                    or (
                        completion_requirement is not None
                        and existing_item.get("completion_requirement")
                        != completion_requirement
                    )
                ):
                    update_module_item(
                        module_id,
                        existing_item["id"],
                        page["order"],
                        completion_requirement
                    )
                    existing_item["position"] = page["order"]
                    if completion_requirement is not None:
                        existing_item["completion_requirement"] = (
                            completion_requirement
                        )
            else:
                add_assignment_to_module(
                    module_id,
                    quiz_id,
                    page["order"],
                    completion_requirement
                )
                module_items.append(
                    {
                        "type": "Assignment",
                        "content_id": quiz_id,
                        "position": page["order"],
                        "completion_requirement": completion_requirement
                    }
                )

            answer_page = find_page(
                page["slug"],
                page["title"],
                existing_pages
            )
            if answer_page:
                current_page = get_page(answer_page["url"])
                if current_page.get("published") is not False:
                    print(
                        f"    Unpublishing old answer page "
                        f"{page['title']}"
                    )
                    set_page_published(
                        answer_page["url"],
                        False
                    )

            continue

        if page["type"].strip().upper() == "ASSIGNMENT":
            assignment = sync_canvas_assignment(
                page,
                assignments,
                assignment_group_id
            )
            assignment_id = assignment["id"]
            assignment_id_key = str(assignment_id)
            existing_item = next(
                (
                    item
                    for item in module_items
                    if item["type"] == "Assignment"
                    and str(item.get("content_id")) == assignment_id_key
                ),
                None
            )

            if existing_item:
                if existing_item["position"] != page["order"]:
                    update_module_item(
                        module_id,
                        existing_item["id"],
                        page["order"]
                    )
                    existing_item["position"] = page["order"]
            else:
                add_assignment_to_module(
                    module_id,
                    assignment_id,
                    page["order"]
                )
                module_items.append(
                    {
                        "type": "Assignment",
                        "content_id": assignment_id,
                        "position": page["order"]
                    }
                )

            old_page = find_page(
                page["slug"],
                page["title"],
                existing_pages
            )
            if old_page:
                current_page = get_page(old_page["url"])
                if current_page.get("published") is not False:
                    print(
                        f"    Unpublishing old portfolio page "
                        f"{page['title']}"
                    )
                    set_page_published(
                        old_page["url"],
                        False
                    )

            continue

        existing_page = find_page(
            page["slug"],
            page["title"],
            existing_pages
        )

        if existing_page:
            page_url = existing_page["url"]
            current_page = get_page(page_url)
            proposed_body = markdown_to_canvas_html(
                page["content"]
            )

            if (current_page.get("body") or "").strip() != proposed_body.strip():
                print(
                    f"    Updating existing page "
                    f"{page['title']}"
                )
                update_page(
                    page_url,
                    proposed_body
                )
            else:
                print(
                    f"    No changes for "
                    f"{page['title']}"
                )
        else:
            page_url = create_page(
                page["title"],
                page["content"]
            )
            existing_pages.append(
                {
                    "title": page["title"],
                    "url": page_url
                }
            )

        desired_page_urls.add(page_url)

        existing_item = next(
            (
                item
                for item in module_items
                if item["type"] == "Page"
                and item.get("page_url") == page_url
            ),
            None
        )

        if existing_item:
            if existing_item["position"] != page["order"]:
                update_module_item(
                    module_id,
                    existing_item["id"],
                    page["order"]
                )
                existing_item["position"] = page["order"]
        else:
            add_page_to_module(
                module_id,
                page_url,
                page["order"]
            )
            module_items.append(
                {
                    "type": "Page",
                    "page_url": page_url,
                    "position": page["order"]
                }
            )

    known_quiz_ids = {
        str(quiz["id"])
        for quiz in quizzes
    }

    for item in module_items:
        content_id = str(item.get("content_id"))
        is_obsolete_quiz = (
            item["type"] == "Assignment"
            and content_id in known_quiz_ids
            and content_id not in desired_quiz_ids
        )
        if (
            (
                item["type"] == "Page"
                and item.get("page_url") not in desired_page_urls
            )
            or is_obsolete_quiz
            or (
                item["type"] == "Quiz"
                and content_id not in desired_quiz_ids
            )
        ):
            print(
                f"  Removing obsolete module item "
                f"{item.get('title', item.get('page_url', item.get('content_id', '')))}"
            )
            delete_module_item(
                module_id,
                item["id"]
            )

##############################################################################
# MAIN
##############################################################################

def parse_args():

    global CANVAS_URL, COURSE_ID, PAGES_DIR, MEDIA_FOLDER, HEADERS

    parser = argparse.ArgumentParser(
        description=(
            "Import Canvas course modules from Markdown session files. "
            "The pages directory must contain session*.md files and "
            "(optionally) a media/ child directory with the images "
            "referenced by those pages."
        )
    )
    parser.add_argument(
        "--course-id",
        type=int,
        default=None,
        help="Canvas course ID (or set CANVAS_COURSE_ID)"
    )
    parser.add_argument(
        "--access-token",
        default=None,
        help=(
            "Canvas API token (or set CANVAS_ACCESS_TOKEN; "
            "falls back to include.py)"
        )
    )
    parser.add_argument(
        "--pages-dir",
        default=None,
        help=(
            "Directory containing the session*.md files and the "
            "media/ child directory (default: <repo>/Pages)"
        )
    )
    parser.add_argument(
        "--canvas-url",
        default=None,
        help="Canvas base URL (default: https://learn.tafe.wa.edu.au)"
    )
    parser.add_argument(
        "--media-folder",
        default=DEFAULT_MEDIA_FOLDER,
        help=(
            f"Course files folder for uploaded media "
            f"(default: {DEFAULT_MEDIA_FOLDER})"
        )
    )
    parser.add_argument(
        "file",
        nargs="?",
        default=None,
        help="Import a single Markdown file instead of all session*.md"
    )

    args = parser.parse_args()

    CANVAS_URL = (
        args.canvas_url
        or os.environ.get("CANVAS_URL")
        or "https://learn.tafe.wa.edu.au"
    )

    access_token = (
        args.access_token
        or os.environ.get("CANVAS_ACCESS_TOKEN")
        or FALLBACK_TOKEN
    )
    if not access_token:
        parser.error(
            "no access token provided: use --access-token or "
            "CANVAS_ACCESS_TOKEN (or provide include.py)"
        )

    course_id = args.course_id
    if course_id is None and os.environ.get("CANVAS_COURSE_ID"):
        course_id = int(os.environ["CANVAS_COURSE_ID"])
    if not course_id:
        parser.error(
            "no course ID provided: use --course-id or CANVAS_COURSE_ID"
        )

    pages_dir = Path(
        args.pages_dir
        or os.environ.get("CANVAS_PAGES_DIR")
        or DEFAULT_PAGES_DIR
    )
    if not pages_dir.is_dir():
        parser.error(
            f"pages directory not found: {pages_dir}"
        )

    COURSE_ID = int(course_id)
    PAGES_DIR = pages_dir
    MEDIA_FOLDER = args.media_folder
    HEADERS = {
        "Authorization": f"Bearer {access_token}"
    }

    return args


def main():

    args = parse_args()

    uploaded = upload_media(
        COURSE_ID,
        PAGES_DIR,
        MEDIA_FOLDER
    )
    if uploaded:
        print(
            f"\nMedia: {len(uploaded)} file(s) from "
            f"{PAGES_DIR / 'media'} "
            f"-> course files folder '{MEDIA_FOLDER}'"
        )
    else:
        print(
            "\nMedia: no media/ directory in "
            f"{PAGES_DIR}; page image references will be left as-is"
        )

    files = get_markdown_files(
        args.file
    )

    for file in files:

        import_file(file)

    print(
        "\nCanvas Publish Complete"
    )

if __name__ == "__main__":
    main()