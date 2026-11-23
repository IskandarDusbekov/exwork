"""Очиқ сайт: блог (видео дарслар бўлимлари, ҳар видеонинг ўз саҳифаси, мақолалар). Мазмуни — ``jobs.site``,
бошқаруви — панел «Сайт: бош саҳифа ва блог» ва «Ёрдам матнлари ва видео». Ҳаммаси киришсиз очилади."""
from django.contrib.auth.decorators import login_not_required
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils.safestring import mark_safe
from django.views.decorators.http import require_safe

from . import pages, site
from .richtext import image_path, plain, sanitize
from django.utils.translation import gettext as _


def _posts():
    from panel.models import Post
    return Post.objects.filter(published=True).select_related("category")


def post_body(post):
    """Мақола матни → HTML: муҳаррирдагиси тозаланади, эски оддий матн — ``render_body``."""
    return mark_safe(sanitize(post.body)) if post.fmt == "html" else site.render_body(post.body)


def post_plain(post) -> str:
    return plain(post.body) if post.fmt == "html" else " ".join(post.body.replace("## ", "").replace("- ", " ").split())


def post_cover(post) -> str:
    """Рўйхатдаги расм: танлангани ёки матндаги биринчи расм."""
    if post.cover:
        return post.cover
    if post.fmt == "html":
        import re
        m = re.search(r'<img[^>]+src="(/img/[^"]+)"', post.body)
        return m.group(1) if m else ""
    return ""


def _decorate(posts):
    """Рўйхат учун: .views, .cover_url, .minutes (ўқиш вақти)."""
    posts = list(posts)
    n = pages.counts("post", [p.pk for p in posts])
    for p in posts:
        p.views = n[str(p.pk)]
        p.cover_url = post_cover(p)
        p.minutes = max(1, round(len(post_plain(p).split()) / 180))
    return posts


def _cats(posts):
    """Мақолалар бўлимлари бўйича: [{c, posts}] (бўлим тартибида), охирида — бўлимсизлари."""
    from panel.models import Category
    by = {}
    for p in posts:
        by.setdefault(p.category_id, []).append(p)
    out = [{"c": c, "posts": by[c.pk]} for c in Category.objects.all() if by.get(c.pk)]
    if by.get(None):
        out.append({"c": None, "posts": by[None]})
    return out


def _video_views(sections):
    n = pages.counts("video", [v["id"] for s in sections for v in s["videos"]])
    for s in sections:
        for v in s["videos"]:
            v["views"] = n[v["id"]]


def _crumbs(request, *items):
    """BreadcrumbList (JSON-LD): [(ном, манзил), …]."""
    return {"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": [
        {"@type": "ListItem", "position": i + 1, "name": name, "item": request.build_absolute_uri(url)}
        for i, (name, url) in enumerate(items)]}


@login_not_required
@require_safe
def blog_index(request):
    """Блог: фақат мақолалар (бўлимлари бўйича сараланади). Видео қўлланма — алоҳида саҳифа (``guide_page``)."""
    from panel.models import Category
    posts = _decorate(_posts()[:200])
    cat = request.GET.get("c", "")
    cats = [c for c in Category.objects.all() if any(p.category_id == c.pk for p in posts)]
    shown = [p for p in posts if p.category and p.category.slug == cat] if cat else posts
    ld = [_crumbs(request, (_("Бош саҳифа"), reverse("index")), (_("Блог"), reverse("blog")))]
    return render(request, "jobs/blog.html", {
        "posts": shown, "total": len(posts), "cats": cats, "cat": cat, "ld": ld,
        "video_count": sum(len(s["videos"]) for s in site.video_sections()),
        "description": _("mhisobot блоги: смета нархлаш, ресурс нархлари ва техник назорат бўйича мақолалар.")})


@login_not_required
@require_safe
def guide_page(request):
    """Видео қўлланма: видеолар бўлимлари бўйича, кейин иш тартиби ва смета ёзувлари изоҳи."""
    sections = site.video_sections()
    _video_views(sections)
    from .guide import FLOW, help_groups
    groups = [{"title": t, "slug": f"t{i + 1}", "items": items} for i, (t, items) in enumerate(help_groups())]
    ld = [_crumbs(request, (_("Бош саҳифа"), reverse("index")), (_("Видео қўлланма"), reverse("guide")))]
    return render(request, "jobs/guide.html", {
        "sections": sections, "video_count": sum(len(s["videos"]) for s in sections), "ld": ld,
        "flow": FLOW, "groups": groups, "term_count": sum(len(g["items"]) for g in groups),
        "post_count": _posts().count(),
        "description": _("mhisobot видео қўлланмаси: смета нархлаш ва техник назоратни қисқа видеоларда ўрганинг.")})


@login_not_required
@require_safe
def blog_video(request, video_id):
    video, section = site.find_video(video_id)
    if not video:
        raise Http404
    url = request.build_absolute_uri(reverse("blog_video", args=[video_id]))
    thumb = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
    plain = " ".join(video["note"].replace("## ", "").replace("- ", " ").split())
    desc = plain[:300] or _("{0} — mhisobot видео қўлланмаси ({1}).").format(video['title'], section['label'])
    obj = {"@context": "https://schema.org", "@type": "VideoObject", "name": video["title"], "description": desc,
           "thumbnailUrl": [thumb], "embedUrl": f"https://www.youtube-nocookie.com/embed/{video_id}",
           "contentUrl": f"https://www.youtube.com/watch?v={video_id}", "url": url, "inLanguage": "uz"}
    if video.get("added"):
        obj["uploadDate"] = video["added"]
    ld = [obj, _crumbs(request, (_("Бош саҳифа"), reverse("index")), (_("Видео қўлланма"), reverse("guide")),
                       (video["title"], reverse("blog_video", args=[video_id])))]
    pages.hit(request, "video", video_id)
    more = [v for v in section["videos"] if v["id"] != video_id][:6]
    n = pages.counts("video", [video_id] + [v["id"] for v in more])
    obj["interactionStatistic"] = {"@type": "InteractionCounter", "interactionType": {"@type": "WatchAction"},
                                   "userInteractionCount": n[video_id]}
    return render(request, "jobs/blog_video.html", {
        "video": video, "section": section, "thumb": thumb, "ld": ld, "description": desc[:300],
        "note": site.render_body(video["note"]) if video["note"] else "", "views": n[video_id],
        "more": [dict(v, views=n[v["id"]]) for v in more]})


@login_not_required
@require_safe
def blog_post(request, slug):
    post = get_object_or_404(_posts(), slug=slug)
    url = request.build_absolute_uri(reverse("blog_post", args=[slug]))
    desc = post.summary or post_plain(post)[:200]
    ld = [{"@context": "https://schema.org", "@type": "Article", "headline": post.title, "description": desc,
           "datePublished": post.created.isoformat(), "dateModified": post.updated.isoformat(), "inLanguage": "uz",
           "mainEntityOfPage": url, "author": {"@type": "Organization", "name": "mhisobot"},
           "publisher": {"@type": "Organization", "name": "mhisobot"}},
          _crumbs(request, (_("Бош саҳифа"), reverse("index")), (_("Блог"), reverse("blog")),
                  (post.title, reverse("blog_post", args=[slug])))]
    pages.hit(request, "post", post.pk)
    cover = post_cover(post)
    if cover:
        ld[0]["image"] = request.build_absolute_uri(cover)
    words = len(post_plain(post).split())
    more = _decorate([p for p in _posts()[:5] if p.pk != post.pk][:4])
    return render(request, "jobs/blog_post.html", {
        "post": post, "body": post_body(post), "ld": ld, "description": desc[:300], "more": more, "cover": cover,
        "views": pages.count("post", post.pk), "minutes": max(1, round(words / 180))})


@login_not_required
@require_safe
def blog_category(request, slug):
    from panel.models import Category
    cat = get_object_or_404(Category, slug=slug)
    posts = _decorate(_posts().filter(category=cat)[:200])
    if not posts:
        raise Http404
    ld = [_crumbs(request, (_("Бош саҳифа"), reverse("index")), (_("Блог"), reverse("blog")),
                  (cat.name, reverse("blog_category", args=[slug])))]
    return render(request, "jobs/blog_category.html", {
        "cat": cat, "posts": posts, "ld": ld, "description": _("{0}: mhisobot мақолалари ({1} та).").format(cat.name, len(posts))})


@login_not_required
@require_safe
def about_page(request):
    d = pages.about()
    ld = [{"@context": "https://schema.org", "@type": "Organization", "name": "mhisobot",
           "url": request.build_absolute_uri("/"), "description": d["lead"][:300]},
          _crumbs(request, (_("Бош саҳифа"), reverse("index")), (_("Биз ҳақимизда"), reverse("about_us")))]
    return render(request, "jobs/about.html", {"a": d, "ld": ld, "description": d["lead"][:300]})


@login_not_required
@require_safe
def terms_page(request):
    t = pages.terms()
    return render(request, "jobs/terms.html", {"t": t, "description": _("mhisobot.uz сайтидан фойдаланиш шартлари.")})


@login_not_required
@require_safe
def site_img(request, name):
    """Панелда юкланган расм (мақола, жамоа сурати). media/ nginx'да очиқ эмас — шу ердан берилади."""
    from .richtext import IMG_TYPES
    p = image_path(name)
    if not p:
        raise Http404
    r = FileResponse(open(p, "rb"), content_type=IMG_TYPES[p.suffix[1:]])
    r["Cache-Control"] = "public, max-age=31536000, immutable"  # номи тасодифий — ўзгармайди
    r["X-Content-Type-Options"] = "nosniff"
    return r
