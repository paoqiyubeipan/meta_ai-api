"""
Image and generic file upload functionality for Meta AI API.
Handles uploads through Meta AI's reverse-engineered rupload service.
"""

import os
import uuid
import mimetypes
import logging
import json
import tempfile
from pathlib import Path
from typing import Dict, Any, Optional
from urllib.parse import quote, unquote

logger = logging.getLogger(__name__)

class ImageUploader:
    """Handles image upload to Meta AI using the rupload protocol."""
    
    UPLOAD_URL = "https://rupload.meta.ai/gen_ai_document_gen_ai_tenant/{upload_session_id}"
    
    def __init__(self, session, cookies: Dict[str, str], access_token: Optional[str] = None):
        """
        Initialize ImageUploader with a requests session.
        
        Args:
            session: Requests session with cookies and headers
            cookies: Dictionary of cookies including datr, abra_sess, etc.
            access_token: OAuth access token extracted from meta.ai page (format: "ecto1:...")
        """
        self.session = session
        self.cookies = cookies
        self.access_token = access_token

    def upload_image_bytes(
        self, image_data: bytes, filename: str, mime_type: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Upload image bytes without requiring callers to manage a temp file."""
        if not image_data:
            return {"success": False, "error": "Image file is empty"}
        safe_name = filename.replace("\\", "/").rsplit("/", 1)[-1] or "image.img"
        suffix = Path(safe_name).suffix or ".img"
        if not (mimetypes.guess_type(filename)[0] or "").startswith("image/"):
            suffix = {
                "image/png": ".png",
                "image/jpeg": ".jpg",
                "image/webp": ".webp",
                "image/gif": ".gif",
            }.get(mime_type, ".jpg")
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as temp:
                temp.write(image_data)
                temp_path = temp.name
            result = self.upload_image(temp_path, filename=safe_name)
            if result and result.get("success"):
                result["file_name"] = safe_name
                result["file_size"] = len(image_data)
                if mime_type:
                    result["mime_type"] = mime_type
            return result
        finally:
            if temp_path:
                try:
                    Path(temp_path).unlink(missing_ok=True)
                except OSError:
                    pass

    def upload_file_bytes(
        self, file_data: bytes, filename: str, mime_type: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Upload arbitrary file bytes through Meta AI's document rupload handler."""
        if not file_data:
            return {"success": False, "error": "File is empty"}
        safe_name = filename.replace("\\", "/").rsplit("/", 1)[-1] or "upload.bin"
        suffix = Path(safe_name).suffix or ".bin"
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as temp:
                temp.write(file_data)
                temp_path = temp.name
            result = self.upload_file(temp_path, mime_type=mime_type, filename=safe_name)
            if result and result.get("success"):
                result["file_name"] = safe_name
                result["file_size"] = len(file_data)
                if mime_type:
                    result["mime_type"] = mime_type
            return result
        finally:
            if temp_path:
                try:
                    Path(temp_path).unlink(missing_ok=True)
                except OSError:
                    pass

    def upload_file(
        self, file_path: str, max_retries: int = 3, mime_type: Optional[str] = None,
        filename: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Upload a non-empty file using Meta AI's genai_document rupload handler."""
        return self._upload_file_path(
            file_path, max_retries, require_image=False, mime_type_override=mime_type,
            filename_override=filename,
        )

    def upload_image(
        self, file_path: str, max_retries: int = 3, filename: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Upload an image, retaining the legacy image-only validation."""
        return self._upload_file_path(
            file_path, max_retries, require_image=True, filename_override=filename
        )

    def _upload_file_path(
        self,
        file_path: str,
        max_retries: int = 3,
        require_image: bool = False,
        mime_type_override: Optional[str] = None,
        filename_override: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Upload a file to Meta AI using the rupload protocol with OAuth authentication.
        
        Args:
            file_path: Path to the file
            max_retries: Maximum number of retry attempts for retriable errors (default: 3)
            require_image: Reject non-image MIME types for the legacy image route.
            
        Returns:
            Dictionary containing:
                - success: bool - Whether the upload succeeded
                - media_id: str - The uploaded file's media ID (further use depends on upstream support)
                - upload_session_id: str - Unique upload session ID
                - file_name: str - Original filename
                - file_size: int - File size in bytes
                - mime_type: str - MIME type of the uploaded file
                - error: str - Error message if upload failed
        """
        # Validate we have the access token
        if not self.access_token:
            return {
                "success": False,
                "error": "Missing access token. Please ensure you're authenticated and the token was extracted from meta.ai page."
            }
        
        # Validate access token format
        if not self.access_token.startswith('ecto1:'):
            return {
                "success": False,
                "error": "Invalid access token format. Expected an ecto1-prefixed token."
            }
        
        # Validate file exists
        if not os.path.exists(file_path):
            return {
                "success": False,
                "error": f"File not found at {file_path}"
            }
        
        filename = Path(filename_override).name if filename_override else os.path.basename(file_path)
        file_size = os.path.getsize(file_path)
        
        # Detect MIME type
        mime_type, _ = mimetypes.guess_type(file_path)
        mime_type = mime_type_override or mime_type
        if not mime_type:
            mime_type = "image/jpeg" if require_image else "application/octet-stream"
        
        # Keep the legacy image method strict; generic files preserve their MIME type.
        if require_image and not mime_type.startswith('image/'):
            return {
                "success": False,
                "error": f"Invalid file type: {mime_type}. Only image files are supported."
            }
        
        # Read file data once
        with open(file_path, 'rb') as f:
            file_data = f.read()
        
        # Retry loop for handling temporary failures
        import requests
        import time
        
        for attempt in range(1, max_retries + 1):
            try:
                # Generate unique upload session ID for each attempt
                upload_session_id = str(uuid.uuid4())
                
                # Construct URL
                url = self.UPLOAD_URL.format(upload_session_id=upload_session_id)
                
                # Build headers with OAuth authentication (using access token from page)
                headers = {
                    'accept': '*/*',
                    'accept-language': 'en-US,en;q=0.5',
                    'authorization': f'OAuth {self.access_token}',  # OAuth header with access token from page
                    'desired_upload_handler': 'genai_document',
                    'ecto_auth_token': 'true',
                    'is_abra_user': 'true',
                    'offset': '0',
                    'origin': 'https://meta.ai',
                    'referer': 'https://meta.ai/',
                    'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36',
                    'x-entity-length': str(file_size),
                    'x-entity-name': quote(filename, safe=''),  # URL-encode filename
                    'x-entity-type': mime_type,
                }
                
                logger.info(f"Uploading {filename} ({file_size} bytes, {mime_type}) - attempt {attempt}/{max_retries}")
                # Create a fresh session without cookies to avoid conflicts with OAuth header
                upload_session = requests.Session()
                
                # POST upload using OAuth authentication only (no cookies)
                response = upload_session.post(
                    url,
                    headers=headers,
                    data=file_data,
                    timeout=30
                )
                
                logger.info(f"Upload response status: {response.status_code}")
                
                # Handle 412 Precondition Failed (AuthorizationFailedError)
                if response.status_code == 412:
                    try:
                        error_data = response.json()
                        debug_info = error_data.get('debug_info', {})
                        is_retriable = debug_info.get('retriable', False)
                        error_type = debug_info.get('type', 'Unknown')
                        error_message = debug_info.get('message', 'Unknown error')
                        
                        logger.error(f"Upload failed: {response.text[:500]}")
                        
                        if is_retriable and attempt < max_retries:
                            wait_time = 2 ** (attempt - 1)  # Exponential backoff: 1s, 2s, 4s
                            logger.warning(f"{error_type}: {error_message}. Retrying in {wait_time}s... (attempt {attempt}/{max_retries})")
                            time.sleep(wait_time)
                            continue
                        else:
                            return {
                                "success": False,
                                "error": f"Upload failed after {attempt} attempts: {error_type} - {error_message}. The ecto_1_sess cookie may be expired or invalid. Please refresh cookies.",
                                "error_type": error_type,
                                "response": error_data
                            }
                    except json.JSONDecodeError:
                        logger.error(f"Could not parse 412 error response: {response.text[:200]}")
                        if attempt < max_retries:
                            wait_time = 2 ** (attempt - 1)
                            logger.warning(f"Retrying in {wait_time}s... (attempt {attempt}/{max_retries})")
                            time.sleep(wait_time)
                            continue
                        else:
                            return {
                                "success": False,
                                "error": f"Upload failed with 412 error after {attempt} attempts. Please refresh ecto_1_sess cookie."
                            }
                
                # Handle other non-200 status codes
                if response.status_code != 200:
                    logger.error(f"Upload failed: {response.text[:500]}")
                    
                    # For 5xx errors, retry
                    if 500 <= response.status_code < 600 and attempt < max_retries:
                        wait_time = 2 ** (attempt - 1)
                        logger.warning(f"Server error {response.status_code}. Retrying in {wait_time}s... (attempt {attempt}/{max_retries})")
                        time.sleep(wait_time)
                        continue
                    
                    response.raise_for_status()
                
                # Parse response
                result = response.json()
                
                # Check if media_id is in the response
                if 'media_id' in result:
                    logger.info(f"Upload successful! media_id: {result['media_id']}")
                    return {
                        "success": True,
                        "media_id": result['media_id'],
                        "upload_session_id": upload_session_id,
                        "file_name": filename,
                        "file_size": file_size,
                        "mime_type": mime_type,
                        "response": result
                    }
                else:
                    return {
                        "success": False,
                        "error": f"Upload succeeded but no media_id returned: {result}",
                        "upload_session_id": upload_session_id,
                        "response": result
                    }
                
            except requests.exceptions.Timeout:
                logger.error(f"Upload timeout on attempt {attempt}/{max_retries}")
                if attempt < max_retries:
                    wait_time = 2 ** (attempt - 1)
                    logger.warning(f"Retrying in {wait_time}s...")
                    time.sleep(wait_time)
                    continue
                else:
                    return {
                        "success": False,
                        "error": f"Upload timeout after {max_retries} attempts"
                    }
            except Exception as e:
                logger.error(f"Error uploading image on attempt {attempt}/{max_retries}: {e}")
                if attempt < max_retries:
                    wait_time = 2 ** (attempt - 1)
                    logger.warning(f"Retrying in {wait_time}s...")
                    time.sleep(wait_time)
                    continue
                else:
                    return {
                        "success": False,
                        "error": f"Error uploading image after {max_retries} attempts: {str(e)}"
                    }
        
        # Should never reach here, but just in case
        return {
            "success": False,
            "error": f"Upload failed after {max_retries} attempts"
        }

    @staticmethod
    def extract_media_id_from_response(response_data: Dict[str, Any]) -> Optional[str]:
        """
        Extract media_id from upload response, searching through nested structures.
        
        Args:
            response_data: Response dictionary from upload API
        
        Returns:
            Media ID if found, None otherwise
        """
        # Check direct media_id field
        if 'media_id' in response_data:
            media_id = response_data.get('media_id')
            if media_id:
                logger.info(f"Found media_id: {media_id}")
                return media_id
        
        # Search through nested structures
        def search_for_media_id(obj, depth=0):
            if depth > 10:  # Prevent infinite recursion
                return None
                
            if isinstance(obj, dict):
                # Check common field names for media IDs
                for key in ['mediaId', 'media_id', 'id', 'uploadId', 'entityId']:
                    if key in obj and obj[key]:
                        return obj[key]
                
                # Recursively search dict values
                for value in obj.values():
                    result = search_for_media_id(value, depth + 1)
                    if result:
                        return result
            elif isinstance(obj, list):
                # Search through list items
                for item in obj:
                    result = search_for_media_id(item, depth + 1)
                    if result:
                        return result
            
            return None
        
        media_id = search_for_media_id(response_data)
        if media_id:
            logger.info(f"Extracted media_id from nested response: {media_id}")
            return media_id
        
        return None

    @staticmethod
    def parse_upload_response(response_text: str) -> Dict[str, Any]:
        """
        Parse upload response, handling various formats (JSON, form-encoded, etc.).
        
        Args:
            response_text: Raw response text from upload endpoint
        
        Returns:
            Parsed response as dictionary
        """
        if not response_text:
            return {}
        
        # Try JSON first
        try:
            return json.loads(response_text)
        except json.JSONDecodeError:
            logger.warning("Response is not JSON, attempting to parse as form-encoded")
        
        # Try form-encoded format
        try:
            result = {}
            for pair in response_text.split('&'):
                if '=' in pair:
                    key, value = pair.split('=', 1)
                    result[key] = value
            return result if result else {}
        except Exception as e:
            logger.warning(f"Failed to parse response as form-encoded: {e}")
            return {}
