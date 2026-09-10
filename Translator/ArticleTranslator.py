from pathlib import Path
import json
from Translator.Translator import Translator
from Utils.Constants import DEFAULT_VALUE, THUMBNAIL_META_KEY
from Utils.SiteProfile import (
  category_term_source,
  keep_short_posts_with_image,
  min_body_length,
)
from Utils.Utility import Utility as U
import re

_IMG_UPLOAD_PATTERN = re.compile(r"wp-content\/uploads\/(.*?)\\")

class ArticleTranslator(Translator):
  # Constructor
  def __init__(self, incomingData):
    super().__init__(incomingData)
    # {guest author term slug -> real display name}, supplied by the Extractor.
    # Empty is valid: resolution then falls back to the term's own text.
    self.guestAuthorNames = {}

  def _getArticleData(self, data):
    text = str(U._html_text_norm(data.get('content:encoded', DEFAULT_VALUE))).replace('"', '\\"')
    return {
      "authorIDs": [],
      "authors": [],
      "authorCleanNames": [],
      "breakingNews": False,
      "commentStatus": self._normalizeCommentStatus(data.get('wp:comment_status')),
      "creator": data.get('dc:creator'),
      "description": U._html_text_norm(data.get('description')),
      "featuredImgID": self._thumbnailID(data.get('wp:postmeta')),
      "id": self.objCount,
      "slug": data.get('wp:post_name', DEFAULT_VALUE),
      "wpPostID": self._normalizeInt(data.get('wp:post_id')),
      "priority": False,
      "modDate": data.get('wp:post_modified_gmt', DEFAULT_VALUE),
      # Fallback only: overwritten by the post's featured image, if it has one,
      # once the attachments have been indexed (resolveFeaturedImages).
      "photoURL": self._checkForImg(text),
      "pubDate": data.get('wp:post_date_gmt', DEFAULT_VALUE),
      "status": self._normalizeStatus(data.get('wp:status')),
      "tags": data.get('category'),
      "categories": [],
      "metadata": data.get('wp:postmeta'),
      "text": text,
      "excerpt": "",
      "title": self._normalizeTitle(U._html_text_norm(data.get('title', DEFAULT_VALUE))),
    }


  # NOTE: using to only load 9k of the article data
  def _shouldSkip(self, obj, debugMode):
    title = obj["title"]
    if (title is None or not self._dataSanityCheck(obj, debugMode) or obj["tags"] == -1):
      return True 
    return False

  def _dataSanityCheck(self, obj, debugMode=False):
    text = obj["text"]
    title = obj["title"]

    lengthCheck = 4 if debugMode else min_body_length()
    isLongEnough = text != DEFAULT_VALUE and len(text) >= lengthCheck
    # An image-only post has a body too short to pass the floor because the
    # picture IS the article -- on a humour site that is the visual gag, not
    # extraction noise. Opt-in, since on a news archive a bodyless post really
    # is junk.
    if not isLongEnough and keep_short_posts_with_image() and self._hasFeaturedImage(obj):
      isLongEnough = True
    isTextNotNull = isLongEnough
    isTitleNotNull = title != DEFAULT_VALUE
    isTitleNotUnderscore = isTitleNotNull and ('_' not in title)

    # There used to be a `'sudoku' not in text` drop here, alongside the
    # crossword/comics category drop already removed from _processTags.
    # Production publishes sudoku, so it belongs in the corpus -- and the check
    # was on the BODY, so it also silently dropped any article that merely
    # mentioned the word. Its only visible effect was an empty Sudoku
    # subsection that looked like a broken section page rather than a filter.
    return isTextNotNull and isTitleNotUnderscore

  def _normalizeStatus(self, value):
    # WordPress publication state. Anything that is not exactly "publish" --
    # draft, pending, private, future, trash -- is content the newsroom has not
    # released, and the formatter withholds a pub_date for it.
    #
    # Defaults to "publish" when the key is absent so an export that predates
    # this field still imports its archive as published rather than blanking
    # ten thousand articles.
    if value is None:
      return "publish"
    normalized = str(value).strip().lower()
    return normalized or "publish"

  def _normalizeCommentStatus(self, value):
    # WordPress exports carry inconsistent casing/whitespace for comment_status
    # ("open", "Open", " closed"). Force a uniform enum here so downstream
    # consumers (and the CMS dropdown) never see variants. Anything that isn't
    # an explicit "closed" defaults to "open", matching the WordPress default.
    if isinstance(value, str) and value.strip().lower() == "closed":
      return "closed"
    return "open"

  def _normalizeTitle(self, title):
    if title is None:
      return None
    if not isinstance(title, str):
      return title
    # WP export payloads sometimes carry slashed quotes in title text.
    return title.replace('\\"', '"').replace("\\'", "'")

  def _normalizeInt(self, value):
    if value in (None, ""):
      return None
    try:
      return int(str(value).strip())
    except (TypeError, ValueError):
      return None
  
  def _hasFeaturedImage(self, obj):
    """True only for a real featured image. `featuredImgID` uses -1 as its
    "unset" sentinel (see _thumbnailID), and -1 is truthy -- a plain truth test
    here rescues bodyless posts that have no image at all."""
    value = obj.get("featuredImgID")
    if value is None:
      return False
    try:
      return int(value) > 0
    except (TypeError, ValueError):
      return False

  def _thumbnailID(self, metadata):
    """The `_thumbnail_id` postmeta value, or -1 when the post has no featured
    image. -1 is WordPress' own "unset" sentinel and what this field carried
    before featured images were read at all."""
    if isinstance(metadata, dict):
      metadata = [metadata]
    elif not isinstance(metadata, list):
      return -1

    for itm in metadata:
      if isinstance(itm, dict) and itm.get('wp:meta_key') == THUMBNAIL_META_KEY:
        thumbnailID = self._normalizeInt(itm.get('wp:meta_value'))
        if thumbnailID is not None:
          return thumbnailID
    return -1

  def resolveFeaturedImages(self, attachmentURLs):
    """Set `photoURL` from the post's WordPress featured image.

    `_thumbnail_id` is what the live site renders as an article's image, so it
    is the authority here. The `_checkForImg` body scan is only a fallback for
    the posts that have no featured image set -- it picks whatever image happens
    to appear first in the body, which is frequently not the same picture, and
    for a post whose body is a single [puzzleme] shortcode (every modern sudoku
    and crossword) it finds nothing at all.
    """
    if not attachmentURLs:
      return 0

    resolved = 0
    for obj in self.objDataDict.values():
      url = attachmentURLs.get(str(obj.get("featuredImgID")))
      if url:
        obj["photoURL"] = url
        resolved += 1
    return resolved

  def _checkForImg(self, text:str):
    matchObj = _IMG_UPLOAD_PATTERN.search(text) 
    if matchObj:
      return matchObj.group(0).replace('\\', '')

  def _processTags(self, obj):
    resultTags = []
    resultCategories = []
    # Read once per article rather than once per term.
    useNicename = category_term_source() == "nicename"
    keepImageOnly = keep_short_posts_with_image()
    try:
      terms = obj["tags"]
      if terms is None or terms == DEFAULT_VALUE:
        terms = []
      elif isinstance(terms, dict):
        terms = [terms]
      elif not isinstance(terms, list):
        terms = []

      for tagData in terms:
        if not isinstance(tagData, dict):
          continue

        nicename = tagData.get("@nicename", DEFAULT_VALUE)
        domain = tagData.get("@domain", DEFAULT_VALUE)
        text = U._html_text_norm(tagData.get("#text", DEFAULT_VALUE))

        isNoTags = obj["tags"] is None or obj["tags"] == DEFAULT_VALUE
        isNoText = obj["text"] is None or obj["text"] == DEFAULT_VALUE
        # A bodyless post is normally junk, and -1 here drops it before the
        # length floor in _dataSanityCheck ever runs -- so the image-only
        # exemption has to be honoured on this path too, or it silently does
        # nothing for exactly the posts it exists for.
        if isNoText and keepImageOnly and self._hasFeaturedImage(obj):
          isNoText = False
        if (isNoTags or isNoText):
          obj["tags"] = -1
          obj["categories"] = -1
          return

        if (domain == "post_tag" and text is not None and text != DEFAULT_VALUE):
          resultTags.append(text)
        elif (domain == "category"):
          # Which half of the term is recorded is per-site: display text is what
          # the CMS seeds its section names from, the nicename slug is the half
          # that survives a site renaming its sections. See Utils.SiteProfile.
          term = nicename if useNicename else text
          if term is not None and term != DEFAULT_VALUE:
            resultCategories.append(term)
        elif (domain == "author"):
          # The term's text is the slug ("beeboop"), not the byline. Resolve it
          # through the guest author export, which holds the real name.
          resolved = self.guestAuthorNames.get(nicename, text)
          cleanName = resolved.translate(str.maketrans('', '', '.-_ ')).lower()
          obj["authorCleanNames"].append(cleanName)
          obj["authors"].append(resolved)

    except (KeyError, TypeError):
      resultTags.append('NO_TAGS')

    resultTags.sort(reverse=True)
    resultCategories.sort(reverse=True)
    obj["tags"] = resultTags
    obj["categories"] = resultCategories
    self._applyCreatorFallback(obj)

  def _applyCreatorFallback(self, obj):
    """Use `<dc:creator>` as the byline when the post has no author term.

    Co-Authors Plus writes `<category domain="author">` terms, but 2651 of the
    10128 posts in the Aug 2026 export -- 26%, concentrated pre-2020 -- predate
    the plugin and carry their author ONLY in `dc:creator`. Reading just the
    terms left every one of them with no byline at all, which is the entire
    "~2635 authorless articles" figure, and it was never irreducible: 2646 of
    the 2651 handles resolve to an author record that already exists.

    The handle is a login (`john.chagaris`), not a display name, so it is fed in
    as a clean name only. ArticleAuthorMatcher resolves that against the authors
    list and overwrites `authors`/`authorIDs` with the real display name, which
    keeps the two columns and the articles_authors join in sync.
    """
    if obj["authorCleanNames"]:
      return

    creator = obj.get("creator")
    if not isinstance(creator, str) or not creator.strip():
      return

    # A handful of handles are full addresses (`a.b@dev.thetriangle.org`); the
    # local part is the login that matches an author record.
    handle = creator.strip().split("@", 1)[0]
    cleanName = handle.translate(str.maketrans('', '', '.-_ ')).lower()
    if not cleanName:
      return

    obj["authorCleanNames"].append(cleanName)
    obj["authors"].append(handle)

  def _processMetadata(self, obj):
    collection = {}
    metadata = obj.get('metadata')
    if metadata is None or metadata == DEFAULT_VALUE:
      obj['metadata'] = collection
      return

    if isinstance(metadata, dict):
      metadata = [metadata]
    elif not isinstance(metadata, list):
      metadata = []

    for itm in metadata:
      if isinstance(itm, dict):
        key, value = itm.get('wp:meta_key'), itm.get('wp:meta_value')
        if isinstance(key, str) and ('yoast' in key):
          collection.update({key: value})

    obj['metadata'] = collection


  def translate(self, on_progress=None):
    debugMode = False
    if on_progress:
      on_progress(f"translating {len(self.source)} articles")
    # Attachment <item>s share the source list with the posts; they are not
    # articles, but they hold the URLs the featured images resolve through.
    attachmentURLs = {}
    for itm in self.source:
      postType = itm.get('wp:post_type') if isinstance(itm, dict) else None
      if postType == 'attachment':
        postID, url = itm.get('wp:post_id'), itm.get('wp:attachment_url')
        if postID and url:
          attachmentURLs[str(postID).strip()] = url
        continue
      self.translateItem(itm, debugMode)
    if on_progress:
      on_progress("resolving featured images")
    self.resolveFeaturedImages(attachmentURLs)

  def translateItem(self, itm, debugMode=False):
    obj = self._getArticleData(itm)
    self._processTags(obj)
    self._processMetadata(obj)
    # NOTE: using to only load 9k of the article data
    if self._shouldSkip(obj, debugMode):
      return None
    self.addObject(obj)
    return obj

  def _log(self, fileDestination):                      
    fileBuckets = []
    dictLen = len(self.objDataDict)
    remainder = dictLen % 1000
    bucketNum = (dictLen // 1000) + (1 if (remainder > 0) else 0)
    fileDir = Path(fileDestination)
    fileDir.mkdir(parents=True, exist_ok=True)

    for i in range(bucketNum):
      fileBuckets.append({})
    
    for i, itm in enumerate(self.objDataDict):
      bucket = i // 1000
      fileBuckets[bucket].update({i: self.objDataDict[i]})
    
    for i in range(len(fileBuckets)):
      bucket = fileBuckets[i]
      filePath = fileDir / f"{i}.json"
      with filePath.open('w+', encoding='utf-8') as file:
        json.dump(bucket, file, indent=4)


 


  
