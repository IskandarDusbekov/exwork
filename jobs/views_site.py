"""Очиқ сайт: блог (видео дарслар бўлимлари, ҳар видеонинг ўз саҳифаси, мақолалар). Мазмуни — ``jobs.site``,
бошқаруви — панел «Сайт: бош саҳифа ва блог» ва «Ёрдам матнлари ва видео». Ҳаммаси киришсиз очилади."""
from django.contrib.auth.decorators import login_not_required
from django.http import Http404
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views.decorators.http import require_safe

from . import site


def _posts():
    from panel.models import Post
    return Post.objects.filter(published=True).select_related("category")


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


def _crumbs(request, *items):
    """BreadcrumbList (JSON-LD): [(ном, манзил), …]."""
    return {"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": [
        {"@type": "ListItem", "position": i + 1, "name": name, "item": request.build_absolute_uri(url)}
        for i, (name, url) in enumerate(items)]}


@login_not_required
@require_safe
def blog_index(request):
    sections = site.video_sections()
    posts = list(_posts()[:60])
    ld = [_crumbs(request, ("Бош саҳифа", reverse("index")), ("Қўлланма ва блог", reverse("blog")))]
    from .guide import FLOW, help_groups
    groups = [{"title": t, "slug": f"t{i + 1}", "items": items} for i, (t, items) in enumerate(help_groups())]
    return render(request, "jobs/blog.html", {
        "sections": sections, "posts": posts, "video_count": sum(len(s["videos"]) for s in sections), "ld": ld,
        "flow": FLOW, "groups": groups, "term_count": sum(len(g["items"]) for g in groups), "cats": _cats(posts),
        "description": "mhisobot қўлланмаси: смета нархлаш ва техник назорат бўйича видео дарслар ва мақолалар."})


@login_not_required
@require_safe
def blog_video(request, video_id):
    video, section = site.find_video(video_id)
    if not video:
        raise Http404
    url = request.build_absolute_uri(reverse("blog_video", args=[video_id]))
    thumb = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
    plain = " ".join(video["note"].replace("## ", "").replace("- ", " ").split())
    desc = plain[:300] or f"{video['title']} — mhisobot видео дарси ({section['label']})."
    obj = {"@context": "https://schema.org", "@type": "VideoObject", "name": video["title"], "description": desc,
           "thumbnailUrl": [thumb], "embedUrl": f"https://www.youtube-nocookie.com/embed/{video_id}",
           "contentUrl": f"https://www.youtube.com/watch?v={video_id}", "url": url, "inLanguage": "uz"}
    if video.get("added"):
        obj["uploadDate"] = video["added"]
    ld = [obj, _crumbs(request, ("Бош саҳифа", reverse("index")), ("Қўлланма ва блог", reverse("blog")),
                       (video["title"], reverse("blog_video", args=[video_id])))]
    return render(request, "jobs/blog_video.html", {
        "video": video, "section": section, "thumb": thumb, "ld": ld, "description": desc[:300],
        "note": site.render_body(video["note"]) if video["note"] else "",
        "more": [v for v in section["videos"] if v["id"] != video_id][:6]})


@login_not_required
@require_safe
def blog_post(request, slug):
    post = get_object_or_404(_posts(), slug=slug)
    url = request.build_absolute_uri(reverse("blog_post", args=[slug]))
    desc = post.summary or post.body.strip().replace("\n", " ")[:200]
    ld = [{"@context": "https://schema.org", "@type": "Article", "headline": post.title, "description": desc,
           "datePublished": post.created.isoformat(), "dateModified": post.updated.isoformat(), "inLanguage": "uz",
           "mainEntityOfPage": url, "author": {"@type": "Organization", "name": "mhisobot"},
           "publisher": {"@type": "Organization", "name": "mhisobot"}},
          _crumbs(request, ("Бош саҳифа", reverse("index")), ("Қўлланма ва блог", reverse("blog")),
                  (post.title, reverse("blog_post", args=[slug])))]
    return render(request, "jobs/blog_post.html", {
        "post": post, "body": site.render_body(post.body), "ld": ld, "description": desc[:300],
        "more": [p for p in _posts()[:5] if p.pk != post.pk][:4]})


@login_not_required
@require_safe
def blog_category(request, slug):
    from panel.models import Category
    cat = get_object_or_404(Category, slug=slug)
    posts = list(_posts().filter(category=cat)[:200])
    if not posts:
        raise Http404
    ld = [_crumbs(request, ("Бош саҳифа", reverse("index")), ("Қўлланма", reverse("blog")),
                  (cat.name, reverse("blog_category", args=[slug])))]
    return render(request, "jobs/blog_category.html", {
        "cat": cat, "posts": posts, "ld": ld, "description": f"{cat.name}: mhisobot мақолалари ({len(posts)} та)."})
