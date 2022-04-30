import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse, ParseResult

import bs4
import frontmatter
import markdownify
import pytz
from PIL import Image
from binx_og_image_generator import generate as generate_og_image
from binx_og_image_generator.generator import Blog as ImageGeneratorBlog
from markdown import markdown
from wordpress_markdown_blog_loader.api import Post
from wordpress_markdown_blog_loader.api import Wordpress, WordpressEndpoint


class Blog(object):
    def __init__(self):
        self.dir: Path = None
        self.path: Path = None
        self.blog: frontmatter.Post = frontmatter.Post(content="")
        self.uploaded_images: dict[str, Image] = {}
        self.markdown_image_pattern = re.compile(
            r'\!\[(?P<alt_text>[^]]*)\]\((?P<url>.*?)(?P<caption>\s*"[^"]*?")?\)'
        )

    @staticmethod
    def load(path: str) -> "Blog":
        result = Blog()
        result.path = Path(path)
        result.dir = Path(path).parent
        if not result.path.exists():
            raise ValueError(f"{path} does not exist")

        with open(path, "r") as f:
            result.blog = frontmatter.load(f)
        return result

    def save(self):
        os.makedirs(self.dir, exist_ok=True)
        with open(self.path, "wb") as f:
            frontmatter.dump(self.blog, f)

    @property
    def slug(self):
        return self.blog.metadata.get("slug")

    @slug.setter
    def slug(self, slug):
        self.blog.metadata["slug"] = slug

    @property
    def author(self):
        return self.blog.metadata.get("author")

    @author.setter
    def author(self, author):
        self.blog.metadata["author"] = author

    @property
    def title(self):
        return self.blog.metadata.get("title")

    @title.setter
    def title(self, title):
        self.blog.metadata["title"] = title

    @property
    def subtitle(self):
        return self.blog.metadata.get("subtitle")

    @subtitle.setter
    def subtitle(self, subtitle):
        self.blog.metadata["subtitle"] = subtitle

    @property
    def status(self):
        return self.blog.metadata.get("status", "draft")

    @status.setter
    def status(self, status):
        self.blog.metadata["status"] = status

    @property
    def content(self):
        return self.blog.content

    @content.setter
    def content(self, content):
        self.blog.content = content

    @property
    def og(self):
        return self.blog.metadata.get("og", {})

    @property
    def image(self):
        return self.blog.metadata.get("image")

    @image.setter
    def image(self, image):
        self.blog.metadata["image"] = image

    @property
    def og_image(self):
        return self.og.get("image")

    @og_image.setter
    def og_image(self, og_image):
        if "og" in self.blog.metadata:
            self.blog.metadata["og"]["image"] = og_image
        else:
            self.blog.metadata["og"] = {"image": og_image}

    @property
    def og_description(self):
        return self.og.get("description")

    @og_description.setter
    def og_description(self, og_description):
        if "og" in self.blog.metadata:
            self.blog.metadata["og"]["description"] = og_description
        else:
            self.blog.metadata["og"] = {"description": og_description}

    @property
    def excerpt(self):
        return self.blog.metadata.get("excerpt")

    @excerpt.setter
    def excerpt(self, excerpt):
        self.blog.metadata["excerpt"] = excerpt

    @property
    def categories(self):
        return self.blog.metadata.get("categories", [])

    @categories.setter
    def categories(self, categories: list[str]):
        self.blog.metadata["categories"] = categories

    @property
    def guid(self):
        return self.blog.metadata.get("guid")

    @guid.setter
    def guid(self, new_guid):
        self.blog.metadata["guid"] = new_guid

    @property
    def date(self) -> datetime:
        return self.blog.metadata.get("date")

    @date.setter
    def date(self, new_date):
        self.blog.metadata["date"] = new_date

    @property
    def banner_path(self) -> Optional[Path]:
        return Path(self.dir).joinpath(self.image) if self.image else None

    @property
    def og_banner_path(self) -> Optional[Path]:
        return Path(self.dir).joinpath(self.og_image) if self.og_image else None

    @property
    def banner(self) -> Optional[Image.Image]:
        path = self.banner_path
        return Image.open(path) if path and path.exists() else None

    @property
    def og_banner(self) -> Optional[Image.Image]:
        path = self.og_banner_path
        return Image.open(path) if path and path.exists() else None

    def generate_og_banner(self):
        in_file = self.banner_path
        out_file = (
            self.og_banner
            if self.og_banner
            else Path(self.dir).joinpath(Path("images/og-banner.jpg"))
        )
        logging.info("generating new image in %s", out_file)
        blog = ImageGeneratorBlog(self.title, self.subtitle, self.author)
        generate_og_image(
            blog, in_file, out_file, resize=True, overwrite=True, gradient_magnitude=0.9
        )

    @property
    def rendered(self):
        def replace_references(match: re.Match):
            image = self.uploaded_images.get(match.group("url"))
            if image:
                caption = ""
                if match.group("caption"):
                    caption = match.group("caption")
                return f"![{match.group('alt_text')}]({image.url}{caption})"
            return match.group(0)

        content = self.markdown_image_pattern.sub(replace_references, self.content)
        return markdown(content, extensions=["fenced_code", "codehilite"])

    @property
    def local_image_references(self) -> set[str]:
        return set(
            map(
                lambda u: u.path,
                filter(
                    lambda u: u.scheme in ["", "file"],
                    map(
                        lambda u: urlparse(u),
                        map(
                            lambda m: m.group("url"),
                            re.finditer(self.markdown_image_pattern, self.content),
                        ),
                    ),
                ),
            )
        )

    def remote_image_references(self, endpoint: WordpressEndpoint) -> set[ParseResult]:
        return set(
            filter(
                lambda u: u.scheme in ["https", "http"]
                and endpoint.is_host_for(u)
                and u.path.startswith("/wp-content/uploads/"),
                map(
                    lambda u: urlparse(u),
                    map(
                        lambda m: m.group("url"),
                        re.finditer(self.markdown_image_pattern, self.content),
                    ),
                ),
            ),
        )

    def download_remote_images(self, wp: Wordpress):
        self.downloaded_images = set()
        for url in self.remote_image_references(wp.endpoint):
            path = Path(self.dir).joinpath("images").joinpath(Path(url.path).name)
            logging.info("downloading %s as %s", url.geturl(), path)
            raw = wp.get_media(url.geturl())
            os.makedirs(path.parent, exist_ok=True)
            with open(path, "wb") as file:
                file.write(raw)
            self.downloaded_images.add(url.geturl())

        def replace_remote_image_references(match: re.Match):
            url = urlparse(match.group("url"))
            name = Path(url.path).name
            if url.geturl() in self.downloaded_images:
                caption = ""
                if match.group("caption"):
                    caption = match.group("caption")
                return f"![{match.group('alt_text')}](./images/{name}{caption})"
            return match.group(0)

        self.content = self.markdown_image_pattern.sub(
            replace_remote_image_references, self.content
        )

    def upload_local_images(self, wp: Wordpress):
        self.uploaded_images = {}
        for filename in self.local_image_references:
            path = Path(self.dir).joinpath(filename)
            if not path.exists():
                logging.warning("%s does not exist", path)
                continue

            image = Image.open(path)
            slug = (
                self.slug
                + "-"
                + re.sub(r"[/\.\\]+", "-", Path(filename).stem).strip("-")
            )
            self.uploaded_images[filename] = wp.upload_media(slug, image)

    def to_wordpress(self, wp: Wordpress) -> dict:
        author = wp.get_unique_user_by_name(self.author)
        self.upload_local_images(wp)
        result = {
            "title": self.title,
            "slug": self.slug,
            "author": author.id,
            "date": self.date.isoformat(),
            "date_gmt": self.date.astimezone(pytz.UTC).isoformat(),
            "content": self.rendered,
            "format": "standard",
            "status": self.status,
            "author": author.id,
            "categories": [wp.categories[c] for c in self.categories],
        }
        if self.og_description:
            result["excerpt"] = self.og_description
        return result

    @staticmethod
    def from_wordpress(post: Post, base_directory: Path, wp: Wordpress) -> "Blog":
        """
        convert a Wordpress post to a FrontMatter post
        """
        categories = {id: name for name, id in wp.categories.items()}

        blog = Blog()
        blog.dir = (
            Path(base_directory)
            .joinpath(str(post.date.year))
            .joinpath(f"{post.date.month:02d}")
            .joinpath(post.slug)
        )
        blog.path = Path(blog.dir).joinpath("index.md")
        blog.title = post.title
        blog.author = wp.get_user_by_id(post.author).name
        blog.guid = post.guid
        blog.categories = [categories[c] for c in post.categories]
        blog.date = post.date
        blog.slug = post.slug
        blog.status = post.status
        if post.excerpt:
            blog.og_description = bs4.BeautifulSoup(post.excerpt, "lxml").text.strip()

        blog.content = markdownify.markdownify(
            post.content,
            STRIP=True,
            MARKDOWN_EXTENSIONS=[
                "markdown.extensions.fenced_code",
                "markdown.extensions.extra",
            ],
            code_language_callback=_code_block_language,
        )
        return blog

    def remove_span_tags(self):
        self.content = remove_span_tags_from_code(self.content)


def remove_span_tags_from_code(markdown: str) -> str:
    r"""
    Removes <span> tags from the markdown code blocks. In some upgrade of Wordpress, the code
    block was corrupted to contain the rendered HTML of the code block. This undoes that.
    If the code block is for HTML, span is left in place.

    >>> doc = "```python\n<span class=bla>spanned code</span>\n```\n"
    >>> remove_span_tags_from_code(doc)
    '```python\nspanned code\n```\n'
    >>> doc = "```html\n<span class=bla>spanned code</span>\n```\n"
    >>> remove_span_tags_from_code(doc)
    '```html\n<span class=bla>spanned code</span>\n```\n'
    """
    if "</span>" not in markdown:
        return markdown

    pattern = re.compile(
        "(^```.*?$)(?P<code>.*?)(^```$)", flags=re.MULTILINE | re.DOTALL | re.VERBOSE
    )

    def _remove_span_tags(match):
        if match.group(0).startswith("```html"):
            return match.group(0)

        return (
            match.group(1)
            + re.sub(r"</?span[^>]*?>", "", match.group("code"))
            + match.group(3)
        )

    return pattern.sub(_remove_span_tags, markdown)


def _code_block_language(code_block: bs4.element.Tag) -> str:
    """
    determines the code block language from tag attribute class
    >>> import bs4
    >>> _code_block_language(bs4.BeautifulSoup('<code class="code language-python">...</code>', 'lxml'))
    'python'
    >>> _code_block_language(bs4.BeautifulSoup('<code class="code">...</code>', 'lxml'))
    ''
    """
    code_tag = code_block.find("code")
    return next(
        map(
            lambda l: l.replace("language-", ""),
            filter(
                lambda c: c.startswith("language-"),
                code_tag.get("class", []) if code_tag else "",
            ),
        ),
        "",
    )