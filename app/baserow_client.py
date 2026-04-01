import requests


class BaserowAuthError(Exception):
    pass


class BaserowAPIError(Exception):
    def __init__(self, message: str, status_code: int = None):
        super().__init__(message)
        self.status_code = status_code


class BaserowClient:
    def __init__(self, base_url: str, email: str, password: str):
        self.base_url = base_url.rstrip("/")
        self.email = email
        self.password = password
        self.token: str = None
        self.refresh_token: str = None
        self._session = requests.Session()
        self.authenticate()

    def authenticate(self) -> None:
        resp = self._session.post(
            f"{self.base_url}/api/user/token-auth/",
            json={"email": self.email, "password": self.password},
            timeout=15,
        )
        if resp.status_code != 200:
            raise BaserowAuthError(
                f"Authentication failed ({resp.status_code}): {resp.text}"
            )
        data = resp.json()
        self.token = data["token"]
        self.refresh_token = data.get("refresh_token")

    def _refresh_token(self) -> None:
        if not self.refresh_token:
            self.authenticate()
            return
        resp = self._session.post(
            f"{self.base_url}/api/user/token-refresh/",
            json={"refresh_token": self.refresh_token},
            timeout=15,
        )
        if resp.status_code != 200:
            # Refresh token expired — fall back to full re-auth
            self.authenticate()
            return
        data = resp.json()
        self.token = data["token"]
        if "refresh_token" in data:
            self.refresh_token = data["refresh_token"]

    def _headers(self) -> dict:
        return {
            "Authorization": f"JWT {self.token}",
            "Content-Type": "application/json",
        }

    def _request(self, method: str, path: str, _retry: bool = True, **kwargs):
        url = f"{self.base_url}{path}"
        kwargs.setdefault("timeout", 30)
        resp = self._session.request(method, url, headers=self._headers(), **kwargs)
        if resp.status_code == 401 and _retry:
            self._refresh_token()
            resp = self._session.request(
                method, url, headers=self._headers(), **kwargs
            )
        if not resp.ok:
            raise BaserowAPIError(
                f"{method} {path} failed ({resp.status_code}): {resp.text}",
                status_code=resp.status_code,
            )
        if resp.status_code == 204:
            return {}
        return resp.json()

    # ------------------------------------------------------------------ #
    # Meta endpoints
    # ------------------------------------------------------------------ #

    @staticmethod
    def _list(data) -> list[dict]:
        return data if isinstance(data, list) else data.get("results", data)

    def get_applications(self) -> list[dict]:
        return self._list(self._request("GET", "/api/applications/"))

    def get_tables(self, database_id: int) -> list[dict]:
        return self._list(self._request("GET", f"/api/database/tables/database/{database_id}/"))

    def get_fields(self, table_id: int) -> list[dict]:
        return self._list(self._request("GET", f"/api/database/fields/table/{table_id}/"))

    # ------------------------------------------------------------------ #
    # Row endpoints
    # ------------------------------------------------------------------ #

    def get_rows(self, table_id: int, params: dict = None) -> dict:
        base_params = {"user_field_names": "true", "size": 200}
        if params:
            base_params.update(params)
        return self._request(
            "GET",
            f"/api/database/rows/table/{table_id}/",
            params=base_params,
        )

    def get_all_rows(self, table_id: int, page_size: int = 200) -> list[dict]:
        results = []
        params = {"user_field_names": "true", "size": page_size}
        path = f"/api/database/rows/table/{table_id}/"
        while path:
            resp = self._request("GET", path, params=params)
            results.extend(resp.get("results", []))
            next_url = resp.get("next")
            if next_url:
                # Strip base_url prefix so _request works with relative path
                path = next_url.replace(self.base_url, "")
                params = {}  # params already encoded in next_url
            else:
                path = None
        return results

    def create_row(self, table_id: int, data: dict) -> dict:
        return self._request(
            "POST",
            f"/api/database/rows/table/{table_id}/",
            params={"user_field_names": "true"},
            json=data,
        )

    def update_row(self, table_id: int, row_id: int, data: dict) -> dict:
        return self._request(
            "PATCH",
            f"/api/database/rows/table/{table_id}/{row_id}/",
            params={"user_field_names": "true"},
            json=data,
        )

    def batch_create_rows(self, table_id: int, items: list[dict]) -> list[dict]:
        chunk_size = 200
        created = []
        for i in range(0, len(items), chunk_size):
            chunk = items[i : i + chunk_size]
            resp = self._request(
                "POST",
                f"/api/database/rows/table/{table_id}/batch/",
                params={"user_field_names": "true"},
                json={"items": chunk},
            )
            created.extend(resp.get("items", []))
        return created

    def upload_file(self, file_stream, filename: str, mime_type: str) -> dict:
        """
        Upload a file to Baserow via POST /api/user-files/upload-file/.
        Returns the file object dict (name, url, is_image, thumbnails, …).
        Uses the session directly (not _request) because this is multipart, not JSON.
        Retries once on 401 like _request does.
        """
        url = f"{self.base_url}/api/user-files/upload-file/"
        headers = {"Authorization": f"JWT {self.token}"}

        def _do_upload():
            file_stream.seek(0)
            return self._session.post(
                url,
                headers=headers,
                files={"file": (filename, file_stream, mime_type)},
                timeout=60,
            )

        resp = _do_upload()
        if resp.status_code == 401:
            self._refresh_token()
            headers["Authorization"] = f"JWT {self.token}"
            resp = _do_upload()
        if not resp.ok:
            raise BaserowAPIError(
                f"File upload failed ({resp.status_code}): {resp.text}",
                status_code=resp.status_code,
            )
        return resp.json()

    # ------------------------------------------------------------------ #
    # Schema management
    # ------------------------------------------------------------------ #

    def create_table(self, database_id: int, name: str) -> dict:
        return self._request(
            "POST",
            f"/api/database/tables/database/{database_id}/",
            json={"name": name},
        )

    def create_field(self, table_id: int, payload: dict) -> dict:
        return self._request(
            "POST",
            f"/api/database/fields/table/{table_id}/",
            json=payload,
        )

    def update_field(self, field_id: int, payload: dict) -> dict:
        return self._request(
            "PATCH",
            f"/api/database/fields/{field_id}/",
            json=payload,
        )
