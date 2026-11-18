# DATABASE_URL → PGHOST/PGPORT/PGUSER/PGPASSWORD/PGDATABASE (backup.sh va restore.sh `source` qiladi).
# Parol buyruq qatorida bo'lmasin: `ps` da serverdagi boshqa foydalanuvchilarga (boshqa saytlar) ko'rinadi.
pg_env() {
    eval "$(DATABASE_URL="$1" python3 -c '
import os, shlex, urllib.parse as u
p = u.urlsplit(os.environ["DATABASE_URL"])
d = {"PGHOST": p.hostname or "localhost", "PGPORT": str(p.port or 5432), "PGUSER": u.unquote(p.username or ""),
     "PGPASSWORD": u.unquote(p.password or ""), "PGDATABASE": u.unquote(p.path.lstrip("/"))}
q = dict(u.parse_qsl(p.query))
if q.get("sslmode"):
    d["PGSSLMODE"] = q["sslmode"]
print("\n".join(f"export {k}={shlex.quote(v)}" for k, v in d.items()))
')"
}

# Bazadagi hamma jadval / ketma-ketlik / ko'rinishni o'chiradi (faqat ulanayotgan foydalanuvchiniki — narxlash).
# Bazani o'chirib-qayta ochish (DROP DATABASE) postgres huquqini talab qiladi, bu esa — oddiy foydalanuvchi bilan ham ishlaydi.
PG_CLEAN_SQL="DO \$\$DECLARE r record; BEGIN
FOR r IN SELECT c.relname, c.relkind FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE n.nspname = 'public' AND c.relkind IN ('r','p','v','m','S') AND pg_get_userbyid(c.relowner) = current_user
LOOP
  EXECUTE format('DROP %s IF EXISTS public.%I CASCADE',
                 CASE r.relkind WHEN 'v' THEN 'VIEW' WHEN 'm' THEN 'MATERIALIZED VIEW' WHEN 'S' THEN 'SEQUENCE' ELSE 'TABLE' END,
                 r.relname);
END LOOP; END\$\$;"
