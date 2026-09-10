import zipfile
from contextlib import contextmanager

from lxml import etree  # type: ignore[attr-defined]
from Utils.Utility import Utility as U
from Utils.Constants import EXPORT_DIR, THUMBNAIL_META_KEY

# Sentinel for "key absent" so dict values of None are handled correctly.
_MISSING = object()

_POST_ITEM_KEYS = frozenset((
  "content:encoded",
  # The byline for every pre-Co-Authors-Plus post; without it they are authorless.
  "dc:creator",
  "wp:comment_status",
  # Publication state. Without it every <item> becomes a live article: 16 posts
  # WordPress was holding as draft, pending or private went public on
  # thetriangle.org, four of them from its five private posts.
  "wp:status",
  "description",
  "wp:post_id",
  "wp:post_name",
  "wp:post_modified_gmt",
  "wp:post_date_gmt",
  "category",
  "wp:postmeta",
  "title",
  # Attachment <item>s ride in the same list; these two are what a featured
  # image resolves through (see ArticleTranslator.resolveFeaturedImages).
  "wp:post_type",
  "wp:attachment_url",
))
_GUEST_AUTHOR_ITEM_KEYS = frozenset(("wp:postmeta",))
_AUTHOR_KEYS = frozenset((
  "wp:author_display_name",
  "wp:author_first_name",
  "wp:author_last_name",
  "wp:author_email",
  "wp:author_login",
))

_WP_NS = "http://wordpress.org/export/1.2/"
_CONTENT_NS = "http://purl.org/rss/1.0/modules/content/"
_DC_NS = "http://purl.org/dc/elements/1.1/"
_CONTENT_ENCODED_TAG = f"{{{_CONTENT_NS}}}encoded"
_DC_CREATOR_TAG = f"{{{_DC_NS}}}creator"
_WP_POSTMETA_TAG = f"{{{_WP_NS}}}postmeta"
_WP_POST_TYPE_TAG = f"{{{_WP_NS}}}post_type"
_WP_POST_ID_TAG = f"{{{_WP_NS}}}post_id"
_WP_ATTACHMENT_URL_TAG = f"{{{_WP_NS}}}attachment_url"
_WP_META_KEY_TAG = f"{{{_WP_NS}}}meta_key"
_WP_META_VALUE_TAG = f"{{{_WP_NS}}}meta_value"
_ARTICLE_SCALAR_TAGS = {
  "title": "title",
  "description": "description",
  _CONTENT_ENCODED_TAG: "content:encoded",
  _DC_CREATOR_TAG: "dc:creator",
  f"{{{_WP_NS}}}comment_status": "wp:comment_status",
  f"{{{_WP_NS}}}status": "wp:status",
  f"{{{_WP_NS}}}post_id": "wp:post_id",
  f"{{{_WP_NS}}}post_name": "wp:post_name",
  f"{{{_WP_NS}}}post_modified_gmt": "wp:post_modified_gmt",
  f"{{{_WP_NS}}}post_date_gmt": "wp:post_date_gmt",
}
_AUTHOR_SCALAR_TAGS = {
  f"{{{_WP_NS}}}author_display_name": "wp:author_display_name",
  f"{{{_WP_NS}}}author_first_name": "wp:author_first_name",
  f"{{{_WP_NS}}}author_last_name": "wp:author_last_name",
  f"{{{_WP_NS}}}author_email": "wp:author_email",
  f"{{{_WP_NS}}}author_login": "wp:author_login",
}
_GUEST_AUTHOR_META_KEYS = frozenset((
  "cap-display_name",
  "cap-user_email",
  "cap-first_name",
  "cap-last_name",
  "cap-user_login",
))


class Extractor:
  @staticmethod
  def buildGuestAuthorNameMap(source):
    """Map a guest author's term slug to their real display name.

    A post credits a guest author with
    `<category domain="author" nicename="cap-beeboop">beeboop</category>`,
    where the element text is the SLUG, not the name. When the slug resembles
    the name ("michael-duffin") the matcher still finds the author, but when it
    is a handle it cannot: `cap-beeboop` is Michael Davis, and all ten of his
    articles arrived with no byline. Only the guest author export carries the
    real name, so it is the authority for this mapping.
    """
    names = {}
    try:
      with Extractor._openXml(source) as handle:
        context = etree.iterparse(handle, events=("end",), tag=("item",), recover=True)
        for _, elem in context:
          slug = elem.findtext(f"{{{_WP_NS}}}post_name")
          title = elem.findtext("title")
          if slug and title and title.strip():
            names.setdefault(slug.strip(), title.strip())
          elem.clear()
          parent = elem.getparent()
          if parent is not None:
            while elem.getprevious() is not None:
              del parent[0]
    except Exception:
      # A missing or unreadable guest author export must not take down the
      # whole run; without the map the previous term-text behaviour applies.
      return {}
    return names

  def __init__(self, posts, guestAuths):
    self.postsFile = posts
    self.guestAuthsFile = guestAuths
    self.data = {
      'auth': None,
      'guestAuth': None,
      'art': None
    }

  # GETTERS/SETTERS
  def getData(self, on_progress=None):
    self._xml2Dict(self.postsFile, self.guestAuthsFile, on_progress)
    U._delete_dir(EXPORT_DIR)
    return self.data

  def _setData(self, key, value):
    self.data[key] = value

  # METHODS
  def _keyFor(self, elem):
    # Reproduce xmltodict's 'prefix:local' element keys (e.g. 'content:encoded').
    # The same handful of tags repeat across every item, so memoize per parse.
    tag = elem.tag
    key = self._keyCache.get(tag)
    if key is None:
      local = tag[tag.rfind('}') + 1:] if '}' in tag else tag
      prefix = elem.prefix
      key = (prefix + ':' + local) if prefix else local
      self._keyCache[tag] = key
    return key

  def _elemToObj(self, elem):
    """Convert an lxml element subtree into an xmltodict-compatible value.

    Conventions matched: attributes -> '@name', mixed/element text -> '#text',
    repeated child tags collapse into a list, empty elements -> None.
    """
    attrib = elem.attrib
    if attrib:
      result = {}
      cache = self._attrCache
      for name, value in attrib.items():
        akey = cache.get(name)
        if akey is None:
          akey = '@' + (name[name.rfind('}') + 1:] if '}' in name else name)
          cache[name] = akey
        result[akey] = value
    else:
      result = None

    if len(elem) == 0:
      text = elem.text
      text = text.strip() if text else ""
      if result is not None:
        if text:
          result["#text"] = text
        return result
      return text or None

    if result is None:
      result = {}
    for child in elem:
      key = self._keyFor(child)
      value = self._elemToObj(child)
      existing = result.get(key, _MISSING)
      if existing is _MISSING:
        result[key] = value
      elif type(existing) is list:
        existing.append(value)
      else:
        result[key] = [existing, value]

    text = elem.text
    if text and text.strip():
      result["#text"] = text.strip()
    return result

  def _elemToSelectedObj(self, elem, keys):
    """Convert only selected direct children, preserving xmltodict shapes."""
    result = {}
    for child in elem:
      key = self._keyFor(child)
      if key not in keys:
        continue

      value = self._elemToObj(child)
      existing = result.get(key, _MISSING)
      if existing is _MISSING:
        result[key] = value
      elif type(existing) is list:
        existing.append(value)
      else:
        result[key] = [existing, value]
    return result

  def _textValue(self, elem):
    text = elem.text
    text = text.strip() if text else ""
    return text or None

  def _appendValue(self, result, key, value):
    existing = result.get(key, _MISSING)
    if existing is _MISSING:
      result[key] = value
    elif type(existing) is list:
      existing.append(value)
    else:
      result[key] = [existing, value]

  def _categoryToObj(self, elem):
    if len(elem):
      return self._elemToObj(elem)

    result = None
    if elem.attrib:
      result = {}
      cache = self._attrCache
      for name, value in elem.attrib.items():
        akey = cache.get(name)
        if akey is None:
          akey = '@' + (name[name.rfind('}') + 1:] if '}' in name else name)
          cache[name] = akey
        result[akey] = value

    text = elem.text
    text = text.strip() if text else ""
    if result is not None:
      if text:
        result["#text"] = text
      return result
    return text or None

  def _postmetaToObj(self, elem):
    if elem.attrib:
      return self._elemToObj(elem)

    result = {}
    for child in elem:
      tag = child.tag
      if tag == _WP_META_KEY_TAG:
        self._appendValue(result, "wp:meta_key", self._textValue(child))
      elif tag == _WP_META_VALUE_TAG:
        self._appendValue(result, "wp:meta_value", self._textValue(child))
      else:
        self._appendValue(result, self._keyFor(child), self._elemToObj(child))
    return result or None

  def _postmetaPair(self, elem):
    if elem.attrib:
      value = self._elemToObj(elem)
      return (value.get("wp:meta_key") if isinstance(value, dict) else None), value

    meta_key = None
    meta_value = None
    for child in elem:
      tag = child.tag
      if tag == _WP_META_KEY_TAG:
        meta_key = self._textValue(child)
      elif tag == _WP_META_VALUE_TAG:
        meta_value = self._textValue(child)
      else:
        value = self._elemToObj(elem)
        return (value.get("wp:meta_key") if isinstance(value, dict) else None), value
    return meta_key, {"wp:meta_key": meta_key, "wp:meta_value": meta_value}

  def _articleItemToObj(self, elem):
    # Callers must have checked wp:post_type == "post" first: ~half of all
    # <item>s are attachments (media), never articles, and they must cost one
    # tag scan rather than a full content normalization/tag/metadata build.
    result = {}
    for child in elem:
      tag = child.tag
      key = _ARTICLE_SCALAR_TAGS.get(tag)
      if key is not None:
        result[key] = self._textValue(child)
      elif tag == "category":
        self._appendValue(result, "category", self._categoryToObj(child))
      elif tag == _WP_POSTMETA_TAG:
        meta_key, value = self._postmetaPair(child)
        if isinstance(meta_key, str) and ("yoast" in meta_key or meta_key == THUMBNAIL_META_KEY):
          self._appendValue(result, "wp:postmeta", value)
    return result

  def _attachmentURLPair(self, elem):
    """(attachment post id, file URL) for a media <item>.

    A post's featured image is only ever a `_thumbnail_id` pointing at one of
    these ids, so this index is what turns that id back into a usable URL.
    """
    postID = None
    url = None
    for child in elem:
      tag = child.tag
      if tag == _WP_POST_ID_TAG:
        postID = self._textValue(child)
      elif tag == _WP_ATTACHMENT_URL_TAG:
        url = self._textValue(child)
    return postID, url

  def _authorToObj(self, elem):
    result = {}
    for child in elem:
      key = _AUTHOR_SCALAR_TAGS.get(child.tag)
      if key is not None:
        result[key] = self._textValue(child)
    return result

  def _guestAuthorItemToObj(self, elem):
    result = {}
    for child in elem:
      if child.tag == _WP_POSTMETA_TAG:
        meta_key, value = self._postmetaPair(child)
        if meta_key in _GUEST_AUTHOR_META_KEYS:
          self._appendValue(result, "wp:postmeta", value)
    return result

  @staticmethod
  @contextmanager
  def _openXml(source):
    # lxml rejects content before the XML declaration; skip any leading
    # whitespace/BOM by seeking to the first '<' (mirrors the old .lstrip()).
    if isinstance(source, tuple):
      zip_ref = zipfile.ZipFile(source[0], "r")
      handle = zip_ref.open(source[1], "r")
    else:
      zip_ref = None
      handle = open(source, "rb")
    try:
      head = handle.read(4096)
      start = head.find(b"<")
      handle.seek(start if start != -1 else 0)
      yield handle
    finally:
      handle.close()
      if zip_ref is not None:
        zip_ref.close()

  def _parse(self, path, items, authors=None, itemKeys=_POST_ITEM_KEYS):
    """Stream `path` with iterparse, converting `item`/`wp:author` subtrees.

    iterparse is filtered to just those tags so we don't pay per-element
    dispatch on the whole document. Processed subtrees and their preceding
    siblings are freed as we go so the document is never fully held in memory.
    """
    self._keyCache = {}
    self._attrCache = {}
    tags = ("item",) if authors is None else ("item", "{*}author")
    with self._openXml(path) as handle:
      context = etree.iterparse(handle, events=("end",), tag=tags, recover=True)
      for _, elem in context:
        if elem.tag == "item":
          items.append(self._elemToSelectedObj(elem, itemKeys))
        else:
          authors.append(self._elemToSelectedObj(elem, _AUTHOR_KEYS))
        elem.clear()
        parent = elem.getparent()
        if parent is not None:
          while elem.getprevious() is not None:
            del parent[0]

  def _translatePosts(self, path, articleTranslator, authorTranslator):
    self._keyCache = {}
    self._attrCache = {}
    # Attachments are interleaved with (and usually follow) the posts that
    # reference them, so featured images can only be resolved once the whole
    # file has been streamed. Returned for that second, in-memory pass.
    attachmentURLs = {}
    with self._openXml(path) as handle:
      context = etree.iterparse(handle, events=("end",), tag=("item", "{*}author"), recover=True)
      for _, elem in context:
        if elem.tag == "item":
          postType = elem.findtext(_WP_POST_TYPE_TAG)
          if postType == "post":
            articleTranslator.translateItem(self._articleItemToObj(elem))
          elif postType == "attachment":
            postID, url = self._attachmentURLPair(elem)
            if postID and url:
              attachmentURLs[postID] = url
        else:
          authorTranslator.translateItem(self._authorToObj(elem))
        elem.clear()
        parent = elem.getparent()
        if parent is not None:
          while elem.getprevious() is not None:
            del parent[0]
    return attachmentURLs

  def _translateGuestAuthors(self, path, guestAuthorTranslator):
    self._keyCache = {}
    self._attrCache = {}
    with self._openXml(path) as handle:
      context = etree.iterparse(handle, events=("end",), tag=("item",), recover=True)
      for _, elem in context:
        guestAuthorTranslator.translateItem(self._guestAuthorItemToObj(elem))
        elem.clear()
        parent = elem.getparent()
        if parent is not None:
          while elem.getprevious() is not None:
            del parent[0]

  def translateData(self, translators, on_progress=None):
    def progress(msg):
      if on_progress:
        on_progress(msg)

    # Built first: posts are translated before the guest authors are parsed, so
    # without this pre-pass an article's author term cannot be resolved to a
    # real name at the point it is needed.
    progress("indexing guest author names")
    translators["articles"].guestAuthorNames = self.buildGuestAuthorNameMap(self.guestAuthsFile)

    progress("parsing/translating wp-posts.xml")
    attachmentURLs = self._translatePosts(self.postsFile, translators["articles"], translators["auth"])
    progress("resolving featured images")
    translators["articles"].resolveFeaturedImages(attachmentURLs)
    progress("parsing/translating wp-guestAuths.xml")
    self._translateGuestAuthors(self.guestAuthsFile, translators["gAuth"])
    return translators

  def _xml2Dict(self, posts, guestAuths, on_progress=None):
    def progress(msg):
      if on_progress:
        on_progress(msg)

    authors, articles, guestAuthors = [], [], []

    progress("parsing wp-posts.xml")
    self._parse(posts, articles, authors)
    progress("parsing wp-guestAuths.xml")
    self._parse(guestAuths, guestAuthors, itemKeys=_GUEST_AUTHOR_ITEM_KEYS)

    progress("indexing authors and articles")
    self._setData('auth', authors)
    self._setData('art', articles)
    self._setData('guestAuth', guestAuthors)
