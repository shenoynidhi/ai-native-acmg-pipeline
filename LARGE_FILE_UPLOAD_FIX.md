# Large File Upload Fix (16MB+ VCF Files)

## Problem
VCF files around 16MB were showing "loading and analyzing" in the chat but nothing happened in the backend. Smaller VCF files (KB size) worked fine.

## Root Causes Identified

1. **Frontend Axios Client - No Timeout Configuration**
   - Default axios timeout is too short for large file uploads
   - No `maxBodyLength` or `maxContentLength` configured
   - File: `frontend/src/lib/api.ts`

2. **Backend - No Chunked File Reading**
   - Large files were being read entirely into memory at once with `await file.read()`
   - Could cause timeout or memory issues
   - File: `src/api/upload.py`

3. **Uvicorn - No Keep-Alive Timeout**
   - Default keep-alive timeout too short for large uploads
   - File: `docker-compose.yml`

## Changes Made

### 1. Frontend - axios Configuration (`frontend/src/lib/api.ts`)
```typescript
const apiClient = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    'Content-Type': 'application/json',
  },
  timeout: 300000, // 5 minutes timeout for large file uploads
  maxContentLength: 100 * 1024 * 1024, // 100MB max content length
  maxBodyLength: 100 * 1024 * 1024, // 100MB max body length
});
```

**Why:** Allows uploading files up to 100MB with 5-minute timeout.

### 2. Frontend - Upload Progress Tracking (`frontend/src/pages/Chat.tsx`)
```typescript
await apiClient.post('/upload', formData, {
  headers: { 'Content-Type': 'multipart/form-data' },
  timeout: 300000, // 5 minutes for large files
  onUploadProgress: (progressEvent) => {
    if (progressEvent.total) {
      const percentCompleted = Math.round((progressEvent.loaded * 100) / progressEvent.total);
      console.log(`Upload progress: ${percentCompleted}%`);
    }
  },
});
```

**Why:** 
- Shows upload progress in console for debugging
- Per-request timeout override for large files
- Better error messages

### 3. Backend - Chunked File Reading (`src/api/upload.py`)
```python
# Read file in chunks to handle large files
with open(filepath, "wb") as f:
    chunk_size = 1024 * 1024  # 1MB chunks
    bytes_written = 0
    while content := await file.read(chunk_size):
        f.write(content)
        bytes_written += len(content)

logger.info(f"File saved successfully. Size: {bytes_written / (1024*1024):.2f} MB")
```

**Why:** 
- Prevents loading entire file into memory at once
- Better for files over 16MB
- Adds logging to track upload progress

### 4. Docker Uvicorn Configuration (`docker-compose.yml`)
```yaml
command: >
  uvicorn src.api.main:app
  --host 0.0.0.0
  --port 8080
  --reload
  --reload-dir /app/src
  --limit-max-requests 0
  --timeout-keep-alive 300
```

**Why:** 
- `--timeout-keep-alive 300`: Keeps connection alive for 5 minutes during upload
- `--limit-max-requests 0`: No request limit (development mode)

## Environment Variables

The system respects `MAX_VCF_SIZE_MB` from `docker-compose.yml`:

```yaml
MAX_VCF_SIZE_MB: ${MAX_VCF_SIZE_MB:-500}
```

Default: 500MB limit

## Testing

### Manual Test
1. Upload a 16MB+ VCF file through the Chat interface
2. Check browser console for upload progress logs
3. Verify file appears in chat and gets parsed

### Automated Test
```bash
# Set your API key
export API_KEY=your-actual-api-key

# Run test script
python test_large_upload.py
```

This creates a dummy 16MB VCF and tests the upload endpoint.

## Troubleshooting

### If uploads still fail:

1. **Check backend logs:**
   ```bash
   docker logs acmg-api -f
   ```
   Look for: "Upload request received", "Saving file to", "File saved successfully"

2. **Check browser console:**
   - Should see "Uploading file: ..., Size: X.XX MB"
   - Should see "Upload progress: X%"

3. **Verify environment:**
   ```bash
   # Check MAX_VCF_SIZE_MB is set
   docker exec acmg-api env | grep MAX_VCF_SIZE_MB
   ```

4. **Network/Proxy issues:**
   - If behind nginx/reverse proxy, check `client_max_body_size`
   - If using cloud load balancer, check timeout settings

## File Size Limits

Current configuration supports:
- ✅ VCF files up to 100MB (frontend limit)
- ✅ BAM files up to 100MB (frontend limit)
- ✅ Backend supports up to 500MB (configurable via `MAX_VCF_SIZE_MB`)

To increase limits:
1. Update `frontend/src/lib/api.ts` - `maxBodyLength` and `maxContentLength`
2. Update `docker-compose.yml` - `MAX_VCF_SIZE_MB` environment variable
3. Increase frontend timeout if needed (currently 5 minutes)

## Related Files Modified

- `frontend/src/lib/api.ts` - Axios client configuration
- `frontend/src/pages/Chat.tsx` - Upload progress and error handling
- `src/api/upload.py` - Chunked file reading and logging
- `docker-compose.yml` - Uvicorn timeout configuration
- `test_large_upload.py` - Test script (new)

## Notes

- The 16MB threshold where issues occurred was likely related to default buffer sizes in the network stack
- Chunked reading prevents memory spikes for large files
- Progress logging helps debug upload issues
- Error messages now surface to the user via alert
