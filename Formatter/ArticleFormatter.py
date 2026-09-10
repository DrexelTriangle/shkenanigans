from Formatter.Formatter import Formatter
from Utils.MediaURL import canonicalize_media_url
import json

class ArticleFormatter(Formatter):
    EXCLUDED_SQL_FIELDS = {"authorCleanNames"}
    CMS_COLUMNS = [
        "id",
        "creation_date",
        "slug",
        "author_ids",
        "authors",
        "breaking_news",
        "comment_status",
        "description",
        "featured_img_id",
        "priority",
        "mod_date",
        "photo_url",
        "pub_date",
        "tags",
        "categories",
        "metadata",
        "text",
        "excerpt",
        "title",
        "focus_keyword",
        "meta_description",
        "seo_title",
    ]
    CMS_SCHEMA = {
        "id": "BIGINT PRIMARY KEY AUTO_INCREMENT",
        "creation_date": "DATETIME",
        "slug": "LONGTEXT",
        "author_ids": "LONGTEXT",
        "authors": "LONGTEXT",
        "breaking_news": "BOOL",
        "comment_status": "VARCHAR(255)",
        "description": "LONGTEXT",
        "featured_img_id": "BIGINT",
        "priority": "BOOL",
        "mod_date": "DATETIME",
        "photo_url": "LONGTEXT",
        "pub_date": "DATETIME",
        "tags": "LONGTEXT",
        "categories": "LONGTEXT",
        "metadata": "LONGTEXT",
        "text": "LONGTEXT",
        "excerpt": "LONGTEXT",
        "title": "LONGTEXT",
        "focus_keyword": "LONGTEXT",
        "meta_description": "LONGTEXT",
        "seo_title": "LONGTEXT",
    }

    # Yoast keeps its editor fields in postmeta, which the translator collects
    # into `metadata`. These three are the ones the CMS editor reads back, so
    # they land in their own columns instead of only inside the metadata blob.
    # Older posts wrote the keyphrase to the _text_input key, so it is a
    # fallback rather than a separate field.
    SEO_META_KEYS = {
        "focus_keyword": ("_yoast_wpseo_focuskw", "_yoast_wpseo_focuskw_text_input"),
        "meta_description": ("_yoast_wpseo_metadesc",),
        "seo_title": ("_yoast_wpseo_title",),
    }

    def __init__(self, articleData):
        super().__init__(articleData)

    def _normalize_obj(self, item):
        return item.data if hasattr(item, "data") else item

    def _normalize_datetime(self, value):
        if value in (None, "", "0000-00-00", "0000-00-00 00:00:00"):
            return None
        return value

    def _seo_columns(self, metadata):
        # Yoast title/description templates ("%%title%% %%sep%% %%sitename%%")
        # are placeholders WordPress expands at render time; they are noise once
        # the post leaves WP, so they drop out rather than reaching an editor.
        if isinstance(metadata, list):
            merged = {}
            for itm in metadata:
                if isinstance(itm, dict):
                    merged.update(itm)
            metadata = merged
        if not isinstance(metadata, dict):
            metadata = {}

        columns = {}
        for column, keys in self.SEO_META_KEYS.items():
            value = None
            for key in keys:
                candidate = metadata.get(key)
                if not isinstance(candidate, str):
                    continue
                candidate = candidate.strip()
                if not candidate or "%%" in candidate:
                    continue
                value = candidate
                break
            columns[column] = value
        return columns

    def _to_cms_row(self, obj):
        creation_date = self._normalize_datetime(
            obj.get("creationDate")
            or obj.get("creation_date")
            or obj.get("pubDate")
            or obj.get("modDate")
        )
        photo_url = obj.get("photoURL")
        # WordPress signals "no featured image" with -1, and it arrives either as
        # an int or as the string "-1" depending on the export path. Neither is a
        # URL, and letting one through means consumers render <img src="-1">
        # instead of falling back to no image, so both normalize to NULL.
        if photo_url is None or str(photo_url).strip() in ("", "-1", "0"):
            photo_url = None
        elif isinstance(photo_url, str):
            photo_url = canonicalize_media_url(photo_url)
            if not photo_url.strip():
                photo_url = None
        else:
            photo_url = None

        metadata = obj.get("metadata")

        # The CMS reads "published" as a non-null pub_date, so withholding the
        # date is what makes a WordPress draft arrive as a CMS draft: editable,
        # dated, and absent from every public path. creation_date above is taken
        # from the same value before this point, so the timeline survives.
        #
        # Absent status means an export from before Extractor collected it;
        # those import as published, matching the old behaviour.
        pub_date = self._normalize_datetime(obj.get("pubDate"))
        if str(obj.get("status", "publish")).strip().lower() != "publish":
            pub_date = None

        return {
            **self._seo_columns(metadata),
            "id": obj.get("id"),
            "creation_date": creation_date,
            "slug": obj.get("slug"),
            "author_ids": obj.get("authorIDs"),
            "authors": obj.get("authors"),
            "breaking_news": obj.get("breakingNews"),
            "comment_status": obj.get("commentStatus"),
            "description": obj.get("description"),
            "featured_img_id": obj.get("featuredImgID"),
            "priority": obj.get("priority"),
            "mod_date": self._normalize_datetime(obj.get("modDate")),
            "photo_url": photo_url,
            "pub_date": pub_date,
            "tags": obj.get("tags"),
            "categories": obj.get("categories"),
            "metadata": metadata,
            "text": obj.get("text"),
            "excerpt": obj.get("excerpt"),
            "title": obj.get("title"),
        }

    def _to_sql_literal(self, value):
        if value is None:
            return "NULL"
        if isinstance(value, bool):
            return "1" if value else "0"
        if isinstance(value, int):
            return str(value)
        if isinstance(value, (dict, list)):
            return self._esc(json.dumps(value, ensure_ascii=False))
        return self._esc(value)

    def _row_values(self, objects, columns):
        for obj in objects:
            row = self._to_cms_row(obj)
            yield f"({', '.join(self._to_sql_literal(row.get(col)) for col in columns)})"

    def iter_format(self, table="articles"):
        objects = [
            obj
            for item in self.data
            for obj in [self._normalize_obj(item)]
            if isinstance(obj, dict)
        ]
        if not objects:
            return

        columns = self.CMS_COLUMNS
        columnDefs = [f"`{column}` {self.CMS_SCHEMA[column]}" for column in columns]
        yield f"CREATE TABLE {table} ({', '.join(columnDefs)});"
        insertPrefix = f"INSERT INTO {table} ({', '.join(f'`{col}`' for col in columns)})"

        yield from self._insert_batches(insertPrefix, self._row_values(objects, columns))

    def format(self, table="articles"):
        self.sqlCommands = list(self.iter_format(table))
        return self.sqlCommands
