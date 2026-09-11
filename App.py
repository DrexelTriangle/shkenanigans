import json
import os
from pathlib import Path
from Extractor import Extractor
from Sanitizer.GuestAuthorPolicy import GuestAuthorPolicy
from Sanitizer.AuthorPolicy import AuthorPolicy
from Sanitizer.AuthorSanitizer import AuthorSanitizer
from Sanitizer.ArticleAuthorMatcher import ArticleAuthorMatcher
from Sanitizer.ArticleContentSanitizer import ArticleContentSanitizer
from Translator.ArticleTranslator import ArticleTranslator
from Translator.AuthorTranslator import AuthorTranslator
from Translator.GuestAuthorTranslator import GuestAuthorTranslator
from Utils.Constants import ZIP_FILE
from Utils.Utility import Utility


class Pipeline:
    def __init__(self, on_start, on_done, on_error, resolve_conflict, select_author):
        self.on_start = on_start
        self.on_done = on_done
        self.on_error = on_error
        self.resolve_conflict = resolve_conflict
        self.select_author = select_author

    def runStep(self, onLoad, onDone, func, *args):
        self.on_start(onLoad)
        try:
            result = func(*args) if args else func()
        except Exception:
            self.on_error(f"Error: {onLoad}")
            raise
        self.on_done(onDone)
        return result

    def extractData(self):
        self.on_start("Extracting...")
        try:
            self.on_start("Extracting... (opening wp-export.zip)")
            extractor = Extractor(*Utility.resolveExportZipMembers(ZIP_FILE))
            result = extractor.getData(on_progress=self.on_start)
        except Exception:
            self.on_error("Error: Extracting...")
            raise
        self.on_done("Extracted")
        return result

    def translateData(self, extracted):
        translators = {
            "articles": ArticleTranslator(extracted["art"]),
            "gAuth": GuestAuthorTranslator(extracted["guestAuth"]),
            "auth": AuthorTranslator(extracted["auth"]),
        }
        # The same resolution the fused path performs, so the two cannot
        # disagree about who wrote an article.
        translators["articles"].guestAuthorNames = Extractor.buildGuestAuthorNameMap(
            Utility.resolveExportZipMembers(ZIP_FILE)[1]
        )
        self.on_start("Translating...")
        try:
            translators["auth"].translate(on_progress=self.on_start)
            translators["gAuth"].translate(on_progress=self.on_start)
            translators["articles"].translate(on_progress=self.on_start)
        except Exception:
            self.on_error("Error: Translating...")
            raise
        self.on_done("Translated")
        return translators

    def extractAndTranslateData(self):
        translators = {
            "articles": ArticleTranslator([]),
            "gAuth": GuestAuthorTranslator([]),
            "auth": AuthorTranslator([]),
        }
        self.on_start("Extracting and translating...")
        try:
            self.on_start("Extracting and translating... (opening wp-export.zip)")
            extractor = Extractor(*Utility.resolveExportZipMembers(ZIP_FILE))
            extractor.translateData(translators, on_progress=self.on_start)
        except Exception:
            self.on_error("Error: Extracting and translating...")
            raise
        self.on_done("Extracted and translated")
        return translators

    def logOutputs(self, translators):
        if os.getenv("WP_TRANSLATOR_LOGS", "").strip().lower() not in ("1", "true", "yes", "on"):
            return

        logTargets = [
            ("Logging articles...", "Logged articles", translators["articles"]._log, Path("logs") / "articles"),
            ("Logging guest authors...", "Logged guest authors", translators["gAuth"]._log, Path("logs") / "gAuth.json"),
            ("Logging authors...", "Logged authors", translators["auth"]._log, Path("logs") / "auth.json"),
        ]
        for onLoad, onDone, func, path in logTargets:
            self.runStep(onLoad, onDone, func, path)

    def sanitizeAuthors(self, translators, key, name):
        authors = translators[key].listAuthors()
        policy = AuthorPolicy(authors) if key == "auth" else GuestAuthorPolicy(authors)
        authSanitizer = AuthorSanitizer(authors, policy)
        self.on_start(f"Sanitizing {name}...")
        authors = authSanitizer.sanitize(resolve_conflict=self.resolve_conflict, on_progress=self.on_start)
        self.on_done(f"Sanitized {name}")
        return authors

    def writeAuthorOutput(self, authors, path, name):
        def outputAuthors():
            Path(path).write_text(
                json.dumps({str(i): authors[i].data for i in range(len(authors))}, indent=4),
                encoding="utf-8",
            )
        self.runStep(f"Writing {name} output...", f"Wrote {name} output", outputAuthors)

    @staticmethod
    def _authorMatchKey(name):
        """Normalized display name used to match a guest author to a WP user.

        Exact string equality is not enough: a WP user's display name is often
        title-cased off the login ("Erik Heyman-meltzer") while the Co-Authors
        Plus record for the same person is typed by hand ("Erik Heyman-Meltzer").
        One capital apart is still one person. This is the same normalization
        the within-pool dedupe already matches on.
        """
        if not name:
            return None
        return Utility.cleanDocument(name, "similarity")

    @staticmethod
    def _absorbGuestAuthor(existing, gAuth):
        """Fold a matched guest author's fields into the WP user record.

        Two deliberately narrow rules:
          - an empty field is filled from the guest record, which is usually
            where a real first/last name lives (the email comes from the WP
            user side almost every time);
          - a name field that differs from the guest's ONLY by case or
            punctuation takes the guest's spelling, because that one was typed
            by a person rather than derived from a login.

        A field that genuinely differs is left alone. A guest record must never
        be able to rename somebody.
        """
        for field in ("display_name", "first_name", "last_name", "email"):
            incoming = gAuth.data.get(field)
            if not incoming:
                continue
            current = existing.data.get(field)
            if not current:
                existing.data[field] = incoming
            elif field != "email" and current != incoming and (
                Pipeline._authorMatchKey(current) == Pipeline._authorMatchKey(incoming)
            ):
                existing.data[field] = incoming

    def combineAndReindexAuthors(self, authors, guestAuthors):
        """Fold the guest-author pool into the WP-user pool.

        The two pools are sanitized independently, so this is the ONLY place a
        person represented on both sides gets collapsed into one row. Matching
        too strictly here emits a second `authors` row for them, and
        `Utility.canonicalizeAuthorLogins` then disambiguates the colliding
        logins by appending the row id -- an author slug ending in its own id
        is the fingerprint of a miss.
        """
        combined = authors
        byName = {}
        for auth in authors:
            byName.setdefault(self._authorMatchKey(auth.data["display_name"]), auth)
        usedIds = {
            auth.data["id"]
            for auth in authors
            if auth.data.get("id") is not None
        }
        nextId = (max(usedIds) + 1) if usedIds else 0
        for gAuth in guestAuthors:
            gAuthKey = self._authorMatchKey(gAuth.data["display_name"])
            existing = byName.get(gAuthKey)
            if existing is not None:
                self._absorbGuestAuthor(existing, gAuth)
                continue
            while nextId in usedIds:
                nextId += 1
            gAuth.data["id"] = nextId
            usedIds.add(nextId)
            nextId += 1
            byName[gAuthKey] = gAuth
            combined.append(gAuth)
        return combined

    def sanitizeArticleAuthors(self, translators, allAuthors, best_guess=False):
        articles = translators["articles"].getObjList()
        articleSanitizer = ArticleAuthorMatcher(articles, allAuthors, best_guess=best_guess)
        self.on_start("Sanitizing article authors...")
        sanitizedArticles = articleSanitizer.sanitize(select_author=self.select_author)
        self.on_done("Sanitized article authors")
        return sanitizedArticles

    def sanitizeArticleContent(self, sanitizedArticles):
        # ArticleContentSanitizer.sanitize now also builds excerpts as part of its
        # (parallelized) per-article pass, so no separate excerpt loop is needed.
        contentSanitizer = ArticleContentSanitizer(sanitizedArticles)
        self.runStep("Sanitizing article content...", "Sanitized article content", contentSanitizer.sanitize)
        return sanitizedArticles

    def writeArticleOutput(self, sanitizedArticles):
        if os.getenv("WP_ARTICLE_OUTPUT_LOGS", "").strip().lower() not in ("1", "true", "yes", "on"):
            return

        def outputArticles():
            output_path = Path("logs/article_output.json")
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(
                json.dumps(
                    {str(i): (sanitizedArticles[i].data if hasattr(sanitizedArticles[i], "data") else sanitizedArticles[i])
                     for i in range(len(sanitizedArticles))},
                    indent=4,
                ),
                encoding="utf-8",
            )
        self.runStep("Writing article output...", "Wrote article output", outputArticles)
