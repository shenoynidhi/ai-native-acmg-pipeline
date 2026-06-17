// ACMG Pipeline - Web UI JavaScript

let currentSessionId = null;
let currentApiKey = null;
let eventSource = null;

// Show/hide sections
function showSection(sectionId) {
    const sections = ['register-section', 'analysis-section', 'progress-section', 'results-section', 'error-section'];
    sections.forEach(id => {
        document.getElementById(id).classList.add('hidden');
    });
    document.getElementById(sectionId).classList.remove('hidden');
}

function showRegister() {
    showSection('register-section');
}

function showAnalysis() {
    showSection('analysis-section');
}

function startNew() {
    // Clean up any existing SSE connection
    if (eventSource) {
        eventSource.close();
        eventSource = null;
    }

    currentSessionId = null;
    document.getElementById('analysis-form').reset();
    showSection('analysis-section');
}

// File input handlers
document.getElementById('vcf-file').addEventListener('change', function(e) {
    const fileName = e.target.files[0]?.name || '';
    document.getElementById('file-name').textContent = fileName;
});

document.getElementById('case-db').addEventListener('change', function(e) {
    const fileName = e.target.files[0]?.name || '';
    document.getElementById('case-db-name').textContent = fileName;
});

// Registration form
document.getElementById('register-form').addEventListener('submit', async (e) => {
    e.preventDefault();

    const formData = {
        email: document.getElementById('email').value,
        name: document.getElementById('name').value,
        organisation: document.getElementById('organisation').value || null
    };

    try {
        const response = await fetch('/register', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(formData)
        });

        const result = await response.json();

        if (response.ok) {
            const resultBox = document.getElementById('register-result');
            resultBox.className = 'result-box success';
            resultBox.innerHTML = `
                <strong>✓ Registration Successful!</strong>
                <p>${result.message}</p>
                <p><strong>Your API Key (save this!):</strong></p>
                <code>${result.api_key}</code>
                <p style="margin-top: 10px;">
                    <button class="btn btn-primary" onclick="useApiKey('${result.api_key}')">
                        Use this key to analyze VCF
                    </button>
                </p>
            `;
            resultBox.classList.remove('hidden');
        } else {
            alert('Registration failed: ' + result.detail);
        }
    } catch (error) {
        alert('Error: ' + error.message);
    }
});

function useApiKey(apiKey) {
    document.getElementById('api-key').value = apiKey;
    currentApiKey = apiKey;
    showSection('analysis-section');
}

// Analysis form
document.getElementById('analysis-form').addEventListener('submit', async (e) => {
    e.preventDefault();

    const apiKey = document.getElementById('api-key').value;
    currentApiKey = apiKey;

    const formData = new FormData();
    formData.append('vcf_file', document.getElementById('vcf-file').files[0]);
    formData.append('genome_build', document.getElementById('genome-build').value);
    formData.append('proband_sex', document.getElementById('proband-sex').value);
    formData.append('clinical_notes', document.getElementById('clinical-notes').value);
    formData.append('patient_hpo_terms', document.getElementById('hpo-terms').value);

    const caseDb = document.getElementById('case-db').files[0];
    if (caseDb) {
        formData.append('case_database_csv', caseDb);
    }

    try {
        showSection('progress-section');
        updateProgress(0, 'Submitting analysis...', 'Uploading VCF file...');

        const response = await fetch('/analyze', {
            method: 'POST',
            headers: { 'X-API-Key': apiKey },
            body: formData
        });

        const result = await response.json();

        if (response.ok) {
            currentSessionId = result.session_id;
            document.getElementById('session-id').textContent = result.session_id;

            // Try SSE first, fallback to polling if SSE fails
            try {
                connectSSE();
            } catch (error) {
                console.error('SSE failed, falling back to polling:', error);
                pollStatus();
            }
        } else {
            showError(result.detail || 'Analysis submission failed');
        }
    } catch (error) {
        showError('Error: ' + error.message);
    }
});

// Progress updates
function updateProgress(percent, status, message, details = '') {
    document.getElementById('progress-percent').textContent = Math.round(percent) + '%';
    document.getElementById('progress-status').textContent = status;
    document.getElementById('progress-message').textContent = message;
    document.getElementById('progress-details').textContent = details;
    document.getElementById('progress-fill').style.width = percent + '%';
}

// Poll status (fallback when SSE not available)
async function pollStatus() {
    if (!currentSessionId || !currentApiKey) return;

    try {
        const response = await fetch(`/status/${currentSessionId}`, {
            headers: { 'X-API-Key': currentApiKey }
        });

        const status = await response.json();

        if (response.ok) {
            updateProgress(
                status.progress_pct,
                status.status,
                status.current_step || 'Processing...',
                status.variant_count ? `Variants: ${status.variant_count}` : ''
            );

            if (status.status === 'complete') {
                showResults(status);
            } else if (status.status === 'failed') {
                showError(status.error || 'Analysis failed');
            } else {
                // Continue polling
                setTimeout(pollStatus, 2000);
            }
        } else {
            showError('Failed to check status');
        }
    } catch (error) {
        showError('Error checking status: ' + error.message);
    }
}

// SSE connection (for real-time progress)
function connectSSE() {
    if (!currentSessionId || !currentApiKey) return;

    // Clean up existing connection
    if (eventSource) {
        eventSource.close();
    }

    // Note: EventSource doesn't support custom headers, so we pass API key as query param
    // In production, use a short-lived token instead
    const url = `/stream/${currentSessionId}?api_key=${encodeURIComponent(currentApiKey)}`;

    eventSource = new EventSource(url);

    eventSource.addEventListener('connected', (e) => {
        console.log('SSE connected:', e.data);
    });

    eventSource.addEventListener('progress', (e) => {
        const data = JSON.parse(e.data);
        updateProgress(
            data.progress * 100,
            data.stage,
            data.message,
            data.variant_id ? `Current: ${data.gene} (${data.variant_id})` : ''
        );
    });

    eventSource.addEventListener('complete', (e) => {
        const data = JSON.parse(e.data);
        eventSource.close();
        eventSource = null;

        // Fetch final status to get report paths
        pollStatusOnce();
    });

    eventSource.addEventListener('failed', (e) => {
        const data = JSON.parse(e.data);
        eventSource.close();
        eventSource = null;
        showError(data.message || 'Analysis failed');
    });

    eventSource.onerror = (e) => {
        console.error('SSE error:', e);
        if (eventSource) {
            eventSource.close();
            eventSource = null;
        }
        // Fall back to polling
        pollStatus();
    };
}

// Poll status once (for final results after SSE completes)
async function pollStatusOnce() {
    if (!currentSessionId || !currentApiKey) return;

    try {
        const response = await fetch(`/status/${currentSessionId}`, {
            headers: { 'X-API-Key': currentApiKey }
        });

        const status = await response.json();

        if (response.ok && status.status === 'complete') {
            showResults(status);
        }
    } catch (error) {
        console.error('Error fetching final status:', error);
    }
}

// Show results
function showResults(status) {
    showSection('results-section');
    document.getElementById('variant-count').textContent = status.variant_count || '-';
    document.getElementById('result-session-id').textContent = currentSessionId;
}

// Download report
async function downloadReport(format) {
    if (!currentSessionId || !currentApiKey) return;

    try {
        const response = await fetch(`/download/${currentSessionId}/${format}`, {
            headers: { 'X-API-Key': currentApiKey }
        });

        if (response.ok) {
            const blob = await response.blob();
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `${currentSessionId}_acmg_report.${format}`;
            document.body.appendChild(a);
            a.click();
            window.URL.revokeObjectURL(url);
            document.body.removeChild(a);
        } else {
            alert('Failed to download report');
        }
    } catch (error) {
        alert('Error downloading report: ' + error.message);
    }
}

// Show error
function showError(message) {
    showSection('error-section');
    document.getElementById('error-message').textContent = message;
}

// Initialize - show registration by default
window.addEventListener('DOMContentLoaded', () => {
    showSection('register-section');
});

