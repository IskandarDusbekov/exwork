"""``{% seo_head %}`` — очиқ саҳифалар <head> и: сарлавҳа, тавсиф, canonical, Open Graph, тасдиқлаш кодлари,
JSON-LD ва аналитика (panel.seo). ``title`` берилса — шу (кириш саҳифалари), бўлмаса — панелдаги бош саҳифа сарлавҳаси."""
import json

from django import template
from django.templatetags.static import static
from django.urls import reverse
from django.utils.safestring import mark_safe

from panel import seo

register = template.Library()


def _ld(data) -> str:
    raw = json.dumps(data, ensure_ascii=False)
    return mark_safe(raw.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026"))


@register.inclusion_tag("panel/_seo_head.html", takes_context=True)
def seo_head(context, title="", canonical="", analytics=True, title_tag=True, description="", image="", og_type="website"):
    request = context.get("request")
    v = seo.values()
    absolute = request.build_absolute_uri if request is not None else (lambda p: p)
    home = absolute(reverse("index"))
    url = absolute(canonical or (request.path if request is not None else "/"))
    site_image = absolute(reverse("og_image")) if seo.image_path() else ""
    # мақола муқоваси / видео расми — ижтимоий тармоқда шу кўринади; бўлмаса — панелдаги умумий расм
    image = (image if image.startswith("http") else absolute(image)) if image else site_image
    org = {"@type": "Organization", "@id": home + "#org", "name": v["site_name"], "url": home,
           "logo": absolute(static("logo-512.png"))}
    if v["org_email"]:
        org["email"] = v["org_email"]
    if v["org_phone"]:
        org["telephone"] = v["org_phone"]
    if v["telegram"]:
        org["sameAs"] = [v["telegram"]]
    if site_image:
        org["image"] = site_image
    graph = {"@context": "https://schema.org", "@graph": [
        org,
        {"@type": "WebSite", "@id": home + "#site", "name": v["site_name"], "url": home, "inLanguage": "uz",
         "description": v["description"], "publisher": {"@id": home + "#org"}},
        {"@type": "SoftwareApplication", "name": v["site_name"], "url": home, "applicationCategory": "BusinessApplication",
         "operatingSystem": "Web", "description": v["description"], "inLanguage": "uz"},
    ]}
    if description:  # блог саҳифалари: ўз тавсифи (қидирув натижасида сарлавҳа остидаги матн)
        v = {**v, "description": description}
    return {"v": v, "title": title or v["title"], "title_tag": title_tag, "url": url, "image": image, "og_type": og_type,
            "ld": _ld(graph), "analytics": analytics and v["index"]}


@register.filter
def ld_json(data):
    """Саҳифанинг қўшимча JSON-LD маълумоти (савол-жавоб, видео, мақола): ``<script type="application/ld+json">`` ичига."""
    return _ld(data)
