from pathlib import Path
from Utils.Constants import *
import zipfile
import shutil
import re
import html

# Precompiled regex patterns to avoid recompilation overhead on hot paths.
_AMP_PATTERN = re.compile("&amp;")
_DOT_PATTERN = re.compile("\\.(?=\\w\\w)")
_AUTHOR_CLEAN_PATTERN = re.compile("^by-|^By-|^By |^by |[^\\w ^'^\\.^-]|_|\\d")
_AUTHOR_SPLIT_PATTERN = re.compile(r",|&|&amp;|\band\b")
_SIMILARITY_PATTERN = re.compile("[^\\w]| |\\d|_")
_FIGURE_PATTERN = re.compile(r"<figure\b[^>]*>.*?</figure>", re.IGNORECASE | re.DOTALL)
_IMG_PATTERN = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
_TAG_PATTERN = re.compile(r"<[^>]+>")
_WHITESPACE_PATTERN = re.compile(r"\s+")
_NONSPACE_PATTERN = re.compile(r"\S+")
_SLUG_PATTERN = re.compile(r"[^a-z0-9]+")
_AUTHOR_CASE_PATTERN = re.compile("^\\w| \\w")
# A handful of WordPress accounts were registered with the byline text as the
# login ("By Nayab Iqbal"), so the slug built from that login carries a "by-"
# the display name never had. _AUTHOR_CLEAN_PATTERN already strips this from
# names; it cannot be reused on a slug, because it also strips digits and would
# rewrite the numeric suffix dedupe_slug adds to break collisions.
#
# Anchored and dash-terminated on purpose: it must not touch a surname that
# begins with those letters, and canonicalize_slug has already collapsed the
# separator, so "By Nayab Iqbal" and "by_nayab_iqbal" both arrive as
# "by-nayab-iqbal" while "Byrne" arrives as "byrne" and is left alone.
_AUTHOR_SLUG_BY_PREFIX = re.compile(r"^by-")

class Utility:
  def _obj_data(item):
    return item.data if hasattr(item, "data") else item

  def canonicalize_slug(value):
    if value is None:
      return ""
    return _SLUG_PATTERN.sub("-", str(value).strip().lower()).strip("-")

  def dedupe_slug(used, base, fallback_prefix, item_id):
    candidate_base = base if base else f"{fallback_prefix}-{item_id}"
    candidate = candidate_base
    if candidate in used:
      candidate = f"{candidate_base}-{item_id}"
    counter = 1
    while candidate in used:
      candidate = f"{candidate_base}-{counter}"
      counter += 1
    used.add(candidate)
    return candidate

  def _id_sort_key(item):
    item_id = Utility._obj_data(item).get("id")
    return (item_id is None, item_id)

  def canonicalizeAuthorLogins(authors):
    used = set()
    sorted_authors = sorted(authors, key=Utility._id_sort_key)
    for author in sorted_authors:
      data = Utility._obj_data(author)
      author_id = data.get("id")
      base = Utility.canonicalize_slug(data.get("login")) or Utility.canonicalize_slug(data.get("display_name"))
      base = _AUTHOR_SLUG_BY_PREFIX.sub("", base)
      data["login"] = Utility.dedupe_slug(used, base, "author", author_id)

  def canonicalizeArticleSlugs(articles):
    used = set()
    sorted_articles = sorted(articles, key=Utility._id_sort_key)
    for article in sorted_articles:
      data = Utility._obj_data(article)
      article_id = data.get("id")
      base = Utility.canonicalize_slug(data.get("slug")) or Utility.canonicalize_slug(data.get("title"))
      data["slug"] = Utility.dedupe_slug(used, base, "article", article_id)

  def cleanDocument(document: str, type: str):
    def uppercaseMatch(match):
      return match.group(0).upper()

    match type:
      case "author_single":
        document = document.split("@")
        document = _AMP_PATTERN.sub("&", document[0])
        document = _DOT_PATTERN.sub(" ", document)
        document = _AUTHOR_CLEAN_PATTERN.sub("", document).strip()
        document = _AUTHOR_CASE_PATTERN.sub(uppercaseMatch, document)
        return document
      case "author_multiple":
        documents = _AUTHOR_SPLIT_PATTERN.split(document)
        return [_AUTHOR_CLEAN_PATTERN.sub("", doc).strip() for doc in documents]
      case "similarity":
        return _SIMILARITY_PATTERN.sub("", document).lower()
      case "article":
        return document
    return document

  def unzip(zipPath):
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zipPath, 'r') as zip_ref:
      zip_ref.extractall(EXPORT_DIR)

  def _find_one(directory, pattern):
    # WP exports vary in naming/nesting (e.g. wp-posts[09-25-2025].xml inside a
    # nested wp-export/ folder), so resolve files by glob rather than exact path.
    matches = sorted(Path(directory).rglob(pattern))
    if not matches:
      raise FileNotFoundError(f"No file matching '{pattern}' under {directory}")
    return matches[0]

  def _find_zip_member(names, prefix):
    matches = sorted(
      name for name in names
      if not name.endswith("/")
      and Path(name).name.startswith(prefix)
      and Path(name).suffix.lower() == ".xml"
    )
    if not matches:
      raise FileNotFoundError(f"No zip member matching '{prefix}*.xml'")
    return matches[0]

  def resolveExportZipMembers(zipPath=ZIP_FILE):
    with zipfile.ZipFile(zipPath, "r") as zip_ref:
      names = zip_ref.namelist()
    return (
      (zipPath, Utility._find_zip_member(names, "wp-posts")),
      (zipPath, Utility._find_zip_member(names, "wp-guestAuths")),
    )

  def resolveExportFiles(exportDir=EXPORT_DIR):
    return (
      Utility._find_one(exportDir, "wp-posts*.xml"),
      Utility._find_one(exportDir, "wp-guestAuths*.xml"),
    )


  def _delete_dir(dir):
    path = Path(dir)
    if not path.exists():
      return
    if path.is_dir():
      shutil.rmtree(path, ignore_errors=True)
      return
    path.unlink(missing_ok=True)

  
  def _html_text_norm(text):
    if text is None:
      return None
    # Both substitutions target '&'-containing entities, so most strings (which
    # have no '&' at all) can skip the two replace passes entirely.
    if '&' not in text:
      return text
    return text.replace('&amp;', '&').replace('&nbsp;', ' ')

  def _build_excerpt(text, max_words=100):
    if text is None:
      return ""

    text_without_media = str(text)
    if _FIGURE_PATTERN.search(text_without_media):
      text_without_media = _FIGURE_PATTERN.sub(" ", text_without_media)
    if _IMG_PATTERN.search(text_without_media):
      text_without_media = _IMG_PATTERN.sub(" ", text_without_media)

    plain = html.unescape(text_without_media) if "&" in text_without_media else text_without_media
    if "<" in plain:
      plain = _TAG_PATTERN.sub(" ", plain)

    words = []
    for match in _NONSPACE_PATTERN.finditer(plain):
      words.append(match.group(0))
      if len(words) > max_words:
        return " ".join(words[:max_words])

    if not words:
      return ""

    return _WHITESPACE_PATTERN.sub(" ", plain).strip()
