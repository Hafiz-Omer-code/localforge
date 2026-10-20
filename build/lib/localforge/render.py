"""Render only LocalForge-owned configuration; never touch /etc service files."""

import os
from pathlib import Path

from . import __version__
from .assets import ensure_adminer
from .paths import atomic_write
from .php import project_override


def q(value):
    return (
        '"'
        + str(value).replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$")
        + '"'
    )


def docroot(project):
    root = Path(project["path"])
    return root / "public" if (root / "public").is_dir() else root


def render_fpm(paths, label, versioned=False):
    php_socket = paths.php_version_socket(label) if versioned else paths.php_socket
    ini = paths.config / "php" / label / "php.ini"
    (ini.parent / "conf.d").mkdir(parents=True, exist_ok=True)
    if not ini.exists():
        atomic_write(
            ini,
            "; LocalForge development-only PHP configuration\n"
            "date.timezone = UTC\ndisplay_errors = On\nlog_errors = On\n"
            "error_reporting = E_ALL\nmemory_limit = 256M\n"
            "upload_max_filesize = 64M\npost_max_size = 64M\n"
            "; Add extension= entries here when your runtime needs them.\n",
        )
    # Do not include system php-fpm pools: that would steal system sockets/ports.
    atomic_write(
        paths.generated / (f"php-fpm-{label}.conf" if versioned else "php-fpm.conf"),
        f"""[global]
pid = {q(paths.runtime / (f"php-{label}.pid" if versioned else "php.pid"))}
error_log = {q(paths.logs / (f"php-{label}-error.log" if versioned else "php-error.log"))}
daemonize = no
[localforge]
listen = {q(php_socket)}
listen.mode = 0600
pm = ondemand
pm.max_children = 8
pm.process_idle_timeout = 10s
catch_workers_output = yes
clear_env = no
""",
    )
    return ini


def render(paths, c):
    g, r, d, logs = paths.generated, paths.runtime, paths.data, paths.logs
    http = c["ports"]["http"]
    ini = render_fpm(paths, c["active_php"] or "default")
    adminer = ensure_adminer(paths)
    atomic_write(
        g / "fastcgi-common.conf",
        """fastcgi_param QUERY_STRING $query_string;
fastcgi_param REQUEST_METHOD $request_method;
fastcgi_param CONTENT_TYPE $content_type;
fastcgi_param CONTENT_LENGTH $content_length;
fastcgi_param REQUEST_URI $request_uri;
fastcgi_param DOCUMENT_URI $document_uri;
fastcgi_param DOCUMENT_ROOT $document_root;
fastcgi_param SERVER_PROTOCOL $server_protocol;
fastcgi_param REQUEST_SCHEME $scheme;
fastcgi_param GATEWAY_INTERFACE CGI/1.1;
fastcgi_param SERVER_SOFTWARE nginx;
fastcgi_param REMOTE_ADDR $remote_addr;
fastcgi_param REMOTE_PORT $remote_port;
fastcgi_param SERVER_ADDR $server_addr;
fastcgi_param SERVER_PORT $server_port;
fastcgi_param SERVER_NAME $server_name;
fastcgi_param REDIRECT_STATUS 200;
""",
    )
    extras = {
        "nginx-http.conf": "# Additional directives inside the nginx http block.\n",
        "apache-extra.conf": "# Additional Apache server directives.\n",
        "my-extra.cnf": "# Extra database options; put options under [mysqld].\n",
        "redis-extra.conf": "# Extra Redis directives.\n",
    }
    for name, text in extras.items():
        if not (paths.config / name).exists():
            atomic_write(paths.config / name, text)
    mime = next(
        (
            Path(x)
            for x in (
                "/etc/nginx/mime.types",
                "/etc/httpd/conf/mime.types",
                "/etc/mime.types",
            )
            if Path(x).exists()
        ),
        None,
    )
    mime_config = (
        f"include {q(mime)};"
        if mime
        else "types { text/html html; text/css css; application/javascript js; image/svg+xml svg; }"
    )
    atomic_write(
        g / "nginx.conf",
        f"""# Managed by LocalForge. Regeneration overwrites this file.
worker_processes 1;
pid {q(r / "nginx.pid")};
error_log {q(logs / "nginx-error.log")};
events {{ worker_connections 1024; }}
http {{
    {mime_config}
    default_type application/octet-stream;
    access_log {q(logs / "nginx-access.log")};
    client_body_temp_path {q(d / "nginx/body")};
    proxy_temp_path {q(d / "nginx/proxy")};
    fastcgi_temp_path {q(d / "nginx/fastcgi")};
    uwsgi_temp_path {q(d / "nginx/uwsgi")};
    scgi_temp_path {q(d / "nginx/scgi")};
    include {q(paths.config / "nginx-http.conf")};
    include {q(g / "projects-nginx.conf")};
}}
""",
    )
    for sub in ("body", "proxy", "fastcgi", "uwsgi", "scgi"):
        (d / "nginx" / sub).mkdir(parents=True, exist_ok=True)
    dbdir = d / "database"
    dbdir.mkdir(exist_ok=True)

    # Option-file quoting is not nginx quoting (dollar signs are literal here).
    def cnf(x):
        return '"' + str(x).replace("\\", "\\\\").replace('"', '\\"') + '"'

    atomic_write(
        g / "my.cnf",
        f"""# Managed by LocalForge. Does not read system option files.
[client]
protocol=socket
socket={cnf(paths.mysql_socket)}
[mysqld]
port={c["ports"]["database"]}
bind-address=127.0.0.1
socket={cnf(paths.mysql_socket)}
pid-file={cnf(r / "mysql.pid")}
datadir={cnf(dbdir)}
log-error={cnf(logs / "database-error.log")}
skip-name-resolve
{("mysqlx=OFF" if c["database_kind"] == "mysql" else "")}
!include {paths.config / "my-extra.cnf"}
""",
    )
    (d / "redis").mkdir(exist_ok=True)
    atomic_write(
        g / "redis.conf",
        f"""bind 127.0.0.1
protected-mode yes
port {c["ports"]["redis"]}
daemonize no
dir {cnf(d / "redis")}
appendonly yes
logfile ""
include {cnf(paths.config / "redis-extra.conf")}
""",
    )
    module_dir = next(
        (
            Path(p)
            for p in (
                "/usr/lib64/httpd/modules",
                "/usr/lib/httpd/modules",
                "/usr/lib/apache2/modules",
            )
            if Path(p).exists()
        ),
        Path("/usr/lib/httpd/modules"),
    )
    modules = (
        "mpm_event",
        "unixd",
        "authz_core",
        "authz_host",
        "dir",
        "mime",
        "log_config",
        "proxy",
        "proxy_fcgi",
        "rewrite",
        "alias",
    )
    module_lines = []
    for mod in modules:
        # Some distributions compile unixd/log_config into the server.
        file = module_dir / f"mod_{mod}.so"
        if file.exists() or mod not in ("unixd", "log_config"):
            module_lines.append(f"LoadModule {mod}_module {cnf(file)}")
    default_root = d / "welcome/public"
    default_root.mkdir(parents=True, exist_ok=True)
    atomic_write(
        default_root / "index.html",
        "<h1>LocalForge is running</h1><p>Open a configured project.test URL.</p>\n",
    )
    mime_line = (
        f"TypesConfig {cnf(mime)}"
        if mime
        else "AddType text/html .html\nAddType text/css .css\nAddType application/javascript .js"
    )
    atomic_write(
        g / "httpd.conf",
        f"""# Managed by LocalForge. Unprivileged, loopback-only Apache.
ServerRoot {cnf(d)}
PidFile {cnf(r / "apache.pid")}
DefaultRuntimeDir {cnf(r)}
Listen 127.0.0.1:{http}
ServerName localhost
User #{os.geteuid()}
Group #{os.getegid()}
{os.linesep.join(module_lines)}
{mime_line}
ErrorLog {cnf(logs / "apache-error.log")}
LogFormat "%h %l %u %t \\"%r\\" %>s %b" common
CustomLog {cnf(logs / "apache-access.log")} common
<Directory />
    Require all denied
</Directory>
<VirtualHost 127.0.0.1:{http}>
ServerName localhost
DocumentRoot {cnf(default_root)}
<Directory {cnf(default_root)}>
Require all granted
DirectoryIndex index.html
</Directory>
AliasMatch "^/_adminer$" {cnf(adminer)}
<LocationMatch "^/_adminer$">
    <RequireAll>
        Require local
        Require expr "%{{HTTP_HOST}} =~ m#^(localhost|127[.]0[.]0[.]1)(:[0-9]+)?$#"
    </RequireAll>
    SetHandler "proxy:unix:{paths.php_socket}|fcgi://localhost/"
</LocationMatch>
</VirtualHost>
Include {cnf(g / "projects-apache.conf")}
Include {cnf(paths.config / "apache-extra.conf")}
""",
    )
    render_projects(paths, c)
    atomic_write(paths.generated / ".format", f"{__version__}\n{paths.runtime}\n")
    return ini


def render_projects(paths, c):
    """Independently replace vhost includes, preserving hand-edited main configs."""
    http = c["ports"]["http"]
    php_socket = paths.php_socket
    overrides = {p["name"]: project_override(p, c) for p in c["projects"]}

    def cnf(x):
        return '"' + str(x).replace("\\", "\\\\").replace('"', '\\"') + '"'

    servers = [
        f"""server {{
    listen 127.0.0.1:{http} default_server;
    server_name _;
    default_type text/plain;
    location / {{
        return 200 "LocalForge is running. Open a configured project.test:{http} URL.\\n";
    }}
    location = /_adminer {{
        if ($host !~ "^(localhost|127[.]0[.]0[.]1)$") {{ return 404; }}
        include {q(paths.generated / "fastcgi-common.conf")};
        fastcgi_pass {q("unix:" + str(paths.php_socket))};
        fastcgi_param SCRIPT_FILENAME {q(paths.data / "welcome/adminer.php")};
        fastcgi_param SCRIPT_NAME /_adminer;
    }}
}}"""
    ]
    for project in c["projects"]:
        root = docroot(project)
        override = overrides[project["name"]]
        php_socket = (
            paths.php_version_socket(override) if override else paths.php_socket
        )
        servers.append(f"""server {{
    listen 127.0.0.1:{http};
    server_name {project["name"]}.test;
    root {q(root)};
    index index.php index.html;
    location / {{ try_files $uri $uri/ /index.php?$query_string; }}
    location ~ /\\.(?!well-known/) {{ deny all; }}
    location ~ \\.php$ {{
        try_files $uri =404;
        fastcgi_pass {q("unix:" + str(php_socket))};
        fastcgi_param SCRIPT_FILENAME $realpath_root$fastcgi_script_name;
        fastcgi_param SCRIPT_NAME $fastcgi_script_name;
        include {q(paths.generated / "fastcgi-common.conf")};
    }}
}}""")
    vhosts = []
    for p in c["projects"]:
        root = docroot(p)
        override = overrides[p["name"]]
        php_socket = (
            paths.php_version_socket(override) if override else paths.php_socket
        )
        vhosts.append(f"""<VirtualHost 127.0.0.1:{http}>
ServerName {p["name"]}.test
DocumentRoot {cnf(root)}
<Directory {cnf(root)}>
    Require all granted
    AllowOverride All
    Options -Indexes +FollowSymLinks
    DirectoryIndex index.php index.html
</Directory>
<FilesMatch "\\.php$">
    SetHandler "proxy:unix:{php_socket}|fcgi://localhost/"
</FilesMatch>
<FilesMatch "^\\.">
    Require all denied
</FilesMatch>
</VirtualHost>""")
    labels = sorted({label for label in overrides.values() if label})
    for label in labels:
        render_fpm(paths, label, versioned=True)
    atomic_write(
        paths.generated / "projects-nginx.conf", os.linesep.join(servers) + "\n"
    )
    atomic_write(
        paths.generated / "projects-apache.conf", os.linesep.join(vhosts) + "\n"
    )
    return labels
