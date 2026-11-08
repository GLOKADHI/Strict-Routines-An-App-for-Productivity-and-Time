"""FastAPI route wrappers that bound multipart parsing before endpoint execution."""
from fastapi import HTTPException
from fastapi.routing import APIRoute
from starlette.formparsers import MultiPartException


class BoundedMultipartRoute(APIRoute):
    max_files = 1
    max_fields = 4
    max_part_size = 64 * 1024

    def get_route_handler(self):
        original_handler = super().get_route_handler()

        async def bounded_handler(request):
            if request.headers.get("content-type", "").lower().startswith("multipart/form-data"):
                try:
                    async with request.form(max_files=self.max_files, max_fields=self.max_fields,
                                            max_part_size=self.max_part_size):
                        return await original_handler(request)
                except MultiPartException as exc:
                    raise HTTPException(status_code=400, detail="Malformed or excessive multipart form data.") from exc
            return await original_handler(request)

        return bounded_handler


class LegacyUploadRoute(BoundedMultipartRoute):
    max_files = 1


class EvidenceUploadRoute(BoundedMultipartRoute):
    max_files = 5
    max_fields = 8
