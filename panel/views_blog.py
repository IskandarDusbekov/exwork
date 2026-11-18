"""Панел → «Блог»: мақолалар, видео дарслар ва бўлимлар — бир жойда. Кўриш — ``is_staff``; ўзгартириш —
суперфойдаланувчи. Мақолалар — ``Post`` (бўлими ``Category``), видеолар — SiteSetting "videos" (сайтда /blog/ ва
иловадаги «▶ Видео» тугмаси шуни ишлатади). Ҳар бир ўзгариш журналга ёзилади."""
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from jobs import pages, richtext, site
from jobs.guide import VIDEO_PAGES, videos, youtube_id

from . import conf
from .audit import log as audit
from .models import Category, Event, Post
from .views import _ctx, staff_required, superuser_required

MAX_POSTS = 500
MAX_VIDEOS = 120
MAX_CATS = 40
TABS = (("posts", "Мақолалар"), ("videos", "Видео қўлланма"), ("cats", "Бўлимлар"))


def _back(tab, **extra):
    q = "&".join(f"{k}={v}" for k, v in extra.items())
    return redirect(reverse("panel:blog") + f"?tab={tab}" + (f"&{q}" if q else ""))


@staff_required
def blog_page(request):
    btab = request.GET.get("tab") if request.GET.get("tab") in dict(TABS) else "posts"
    cats = list(Category.objects.all())
    posts = Post.objects.select_related("category")
    cat = request.GET.get("cat", "")
    if cat == "none":
        posts = posts.filter(category__isnull=True)
    elif cat.isdigit():
        posts = posts.filter(category_id=int(cat))
    labels = dict(VIDEO_PAGES)
    vids = [{**v, "section": labels.get(v["page"], "Умумий") if v["page"] else "Умумий дарслар"} for v in videos()]
    counts = {c.pk: c.posts.count() for c in cats}
    posts = list(posts[:MAX_POSTS])
    pv = pages.counts("post", [p.pk for p in posts])
    vv = pages.counts("video", [v["id"] for v in vids])
    for p in posts:
        p.views = pv[str(p.pk)]
    for v in vids:
        v["views"] = vv[v["id"]]
    return render(request, "panel/blog.html", _ctx(
        request, "blog", btab=btab, tabs=TABS, cats=[{"c": c, "n": counts[c.pk]} for c in cats], cat=cat,
        posts=posts, n_posts=Post.objects.count(), n_pub=Post.objects.filter(published=True).count(),
        vids=vids, n_vids=len(vids), views_posts=sum(pv.values()), views_vids=sum(vv.values())))


# ===================== мақолалар =====================
@staff_required
def post_edit(request, post_id=None):
    post = get_object_or_404(Post, pk=post_id) if post_id else Post(author=request.user)
    if request.method == "POST":
        if not request.user.is_superuser:
            raise PermissionDenied("Фақат суперфойдаланувчи")
        return _post_save(request, post)
    return _post_form(request, post)


def _post_form(request, post):
    """Муҳаррирга: эски оддий матнли мақола HTML'га айлантириб берилади (сақлангач — "html" бўлади)."""
    html = post.body if post.fmt == "html" else "".join([site.render_body(post.body)])  # оддий str: textarea’да экранлансин
    return render(request, "panel/blog_post.html", _ctx(
        request, "blog", post=post, cats=Category.objects.all(), body_html=html,
        views=pages.count("post", post.pk) if post.pk else 0))


def _post_save(request, post):
    title = " ".join(request.POST.get("title", "").split())[:160]
    body = richtext.sanitize(request.POST.get("body", ""))
    slug = site.slugify(request.POST.get("slug") or title)
    err = ""
    if not title or not richtext.plain(body) and "<img" not in body:
        err = "сарлавҳа ва матн бўш бўлмасин"
    elif not slug or slug in site.RESERVED_SLUGS:
        err = "манзил (лотин ҳарфлар) бўш ёки банд сўз — бошқасини ёзинг"
    elif Post.objects.filter(slug=slug).exclude(pk=post.pk).exists():
        err = f"«{slug}» манзили бошқа мақолада бор"
    post.title, post.slug, post.body, post.fmt = title, slug, body, "html"
    cover = request.POST.get("cover", "").strip()
    post.cover = cover if cover.startswith("/img/") and richtext.IMG_NAME.match(cover[5:]) else ""
    post.summary = " ".join(request.POST.get("summary", "").split())[:300]
    post.published = request.POST.get("published") == "1"
    cid = request.POST.get("category", "")
    post.category = Category.objects.filter(pk=cid).first() if cid.isdigit() else None
    if err:  # ёзилгани йўқолмасин — формани шу ҳолида қайта кўрсатамиз
        messages.error(request, f"Мақола сақланмади: {err}")
        return _post_form(request, post)
    post.save()
    audit(request, Event.Kind.ADMIN_SETTINGS, detail=f"Блог: «{title[:120]}» {'чоп этилди' if post.published else 'қоралама'}")
    messages.success(request, f"Мақола сақланди: «{title}»" + ("" if post.published else " (қоралама — сайтда кўринмайди)"))
    return redirect("panel:blog_post_edit", post_id=post.pk)


@require_POST
@superuser_required
def post_delete(request, post_id):
    post = get_object_or_404(Post, pk=post_id)
    title = post.title
    post.delete()
    audit(request, Event.Kind.ADMIN_SETTINGS, detail=f"Блог: «{title[:120]}» ўчирилди")
    messages.success(request, f"Мақола ўчирилди: «{title}»")
    return _back("posts")


@require_POST
@staff_required
def preview(request):
    """Матн қандай кўринишини кўрсатади (сайтдаги қоидалар билан, ҳамма матн экранланган)."""
    return HttpResponse(site.render_body(request.POST.get("body", "")[:60000]))


@require_POST
@superuser_required
def upload(request):
    """Муҳаррирдан расм: {"url": "/img/…"} ёки {"error": "…"}."""
    f = request.FILES.get("file")
    if not f:
        return JsonResponse({"error": "файл йўқ"}, status=400)
    try:
        url = richtext.save_image(f)
    except ValueError as e:
        return JsonResponse({"error": str(e)}, status=400)
    audit(request, Event.Kind.ADMIN_SETTINGS, detail=f"Сайт: расм юкланди {url}")
    return JsonResponse({"url": url})


# ===================== видео дарслар =====================
def _stored_videos():
    return [dict(v) for v in (conf.raw("videos", []) or []) if isinstance(v, dict) and v.get("id")]


def _find(vid):
    return next((v for v in videos() if v["id"] == vid), None)


@staff_required
def video_edit(request, vid=None):
    video = _find(vid) if vid else {"title": "", "id": "", "page": "", "note": ""}
    if video is None:
        raise Http404
    if request.method == "POST":
        if not request.user.is_superuser:
            raise PermissionDenied("Фақат суперфойдаланувчи")
        return _video_save(request, vid)
    return render(request, "panel/blog_video.html", _ctx(request, "blog", video=video, orig=vid or "", pages=VIDEO_PAGES,
                                                         url=f"https://youtu.be/{video['id']}" if video["id"] else ""))


def _video_save(request, orig):
    url, title = request.POST.get("url", "").strip(), " ".join(request.POST.get("title", "").split())[:120]
    page = request.POST.get("page", "")
    page = page if page in dict(VIDEO_PAGES) else ""
    note = request.POST.get("note", "").replace("\r", "").strip()[:20000]
    new_id = youtube_id(url)
    items = _stored_videos()
    err = ""
    if not new_id:
        err = "YouTube ҳаволаси эмас (масалан: https://youtu.be/… ёки https://www.youtube.com/watch?v=…)"
    elif not title:
        err = "номини ёзинг"
    elif new_id != orig and any(v["id"] == new_id for v in items):
        err = "бу видео аллақачон қўшилган"
    elif not orig and len(items) >= MAX_VIDEOS:
        err = f"кўпи билан {MAX_VIDEOS} та видео"
    if err:
        messages.error(request, f"Видео сақланмади: {err}")
        return render(request, "panel/blog_video.html", _ctx(
            request, "blog", video={"title": title, "id": new_id or orig, "page": page, "note": note}, orig=orig,
            pages=VIDEO_PAGES, url=url))
    old = next((v for v in items if v["id"] == orig), None) if orig else None
    rec = {"title": title, "id": new_id, "page": page, "note": note,
           "added": (old or {}).get("added") or timezone.localdate().isoformat()}
    if old:
        items[items.index(old)] = rec
    else:
        items.append(rec)
    conf.put("videos", items, request.user)
    audit(request, Event.Kind.ADMIN_SETTINGS, detail=f"Видео дарс: «{title[:120]}» {'янгиланди' if old else 'қўшилди'}")
    messages.success(request, f"Видео сақланди: «{title}»")
    return redirect("panel:blog_video_edit", vid=new_id)


@require_POST
@superuser_required
def video_delete(request, vid):
    items = _stored_videos()
    left = [v for v in items if v["id"] != vid]
    if len(left) == len(items):
        raise Http404
    conf.put("videos", left, request.user)
    audit(request, Event.Kind.ADMIN_SETTINGS, detail=f"Видео дарс ўчирилди: {vid}")
    messages.success(request, "Видео ўчирилди")
    return _back("videos")


@require_POST
@superuser_required
def video_move(request, vid, direction):
    """Тартиб: сайтда ва иловада шу тартибда чиқади (бўлим ичида)."""
    items = _stored_videos()
    i = next((k for k, v in enumerate(items) if v["id"] == vid), None)
    if i is None:
        raise Http404
    j = i - 1 if direction == "up" else i + 1
    if 0 <= j < len(items):
        items[i], items[j] = items[j], items[i]
        conf.put("videos", items, request.user)
    return _back("videos")


# ===================== бўлимлар =====================
@require_POST
@superuser_required
def cat_save(request):
    cid = request.POST.get("id", "")
    cat = get_object_or_404(Category, pk=cid) if cid.isdigit() else Category()
    name = " ".join(request.POST.get("name", "").split())[:80]
    slug = site.slugify(request.POST.get("slug") or name)
    if not name or not slug:
        messages.error(request, "Бўлим сақланмади: номини ёзинг")
    elif Category.objects.filter(slug=slug).exclude(pk=cat.pk).exists():
        messages.error(request, f"Бўлим сақланмади: «{slug}» манзили банд")
    elif not cat.pk and Category.objects.count() >= MAX_CATS:
        messages.error(request, f"Кўпи билан {MAX_CATS} та бўлим")
    else:
        order = request.POST.get("order", "")
        cat.name, cat.slug = name, slug
        cat.order = int(order) if order.isdigit() else (cat.order if cat.pk else Category.objects.count() + 1)
        cat.save()
        audit(request, Event.Kind.ADMIN_SETTINGS, detail=f"Блог бўлими: «{name}»")
        messages.success(request, f"Бўлим сақланди: «{name}»")
    return _back("cats")


@require_POST
@superuser_required
def cat_delete(request, cat_id):
    cat = get_object_or_404(Category, pk=cat_id)
    name = cat.name
    cat.delete()  # мақолалари ўчмайди — бўлимсиз қолади
    audit(request, Event.Kind.ADMIN_SETTINGS, detail=f"Блог бўлими ўчирилди: «{name}»")
    messages.success(request, f"Бўлим ўчирилди: «{name}» (мақолалари «бўлимсиз» бўлиб қолди)")
    return _back("cats")
