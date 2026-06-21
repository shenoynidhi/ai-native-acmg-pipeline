import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { CheckCircle, FileEdit } from 'lucide-react';
import axios from 'axios';

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api';

export default function Register() {
  const navigate = useNavigate();
  const [formData, setFormData] = useState({
    name: '',
    email: '',
    organization: '',
  });
  const [generatedKey, setGeneratedKey] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [copied, setCopied] = useState(false);

  const handleRegister = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');

    if (!formData.name || !formData.email) {
      setError('Name and email are required');
      return;
    }

    setLoading(true);

    try {
      const response = await axios.post(`${API_BASE_URL}/register`, formData);
      setGeneratedKey(response.data.api_key);
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Registration failed. Please try again.');
    } finally {
      setLoading(false);
    }
  };

  const handleCopyAndLogin = () => {
    navigator.clipboard.writeText(generatedKey);
    setCopied(true);
    setTimeout(() => {
      localStorage.setItem('api_key', generatedKey);
      navigate('/chat');
    }, 1000);
  };

  if (generatedKey) {
    return (
      <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '20px' }}>
        <div style={{ background: 'white', borderRadius: '12px', padding: '32px', boxShadow: '0 4px 12px rgba(0,0,0,0.1)', maxWidth: '450px', width: '100%' }}>

          {/* Success Header */}
          <div style={{ textAlign: 'center', marginBottom: '24px' }}>
            <div style={{ width: '64px', height: '64px', background: 'linear-gradient(135deg, #10b981 0%, #14b8a6 100%)', borderRadius: '16px', display: 'flex', alignItems: 'center', justifyContent: 'center', margin: '0 auto 16px', boxShadow: '0 4px 12px rgba(16, 185, 129, 0.3)' }}>
              <CheckCircle size={36} color="white" strokeWidth={2.5} />
            </div>
            <h1 style={{ fontSize: '24px', fontWeight: 'bold', marginBottom: '8px', color: '#059669' }}>
              Registration Successful!
            </h1>
            <p style={{ color: '#6b7280', fontSize: '14px' }}>Save your API key - it won't be shown again</p>
          </div>

          {/* API Key Display */}
          <div style={{ background: '#d1fae5', border: '1px solid #6ee7b7', borderRadius: '8px', padding: '16px', marginBottom: '16px' }}>
            <p style={{ fontFamily: 'monospace', fontSize: '13px', color: '#065f46', wordBreak: 'break-all', lineHeight: '1.5' }}>
              {generatedKey}
            </p>
          </div>

          <p style={{ fontSize: '13px', color: '#6b7280', textAlign: 'center', marginBottom: '20px' }}>
            Copy this key and store it securely. You'll need it to log in.
          </p>

          <button
            onClick={handleCopyAndLogin}
            style={{ width: '100%', padding: '12px', background: 'linear-gradient(135deg, #10b981 0%, #14b8a6 100%)', color: 'white', border: 'none', borderRadius: '8px', fontSize: '14px', fontWeight: '600', cursor: 'pointer', boxShadow: '0 2px 4px rgba(16, 185, 129, 0.3)' }}
          >
            {copied ? 'Copied! Redirecting...' : 'Copy Key & Continue to Chat'}
          </button>
        </div>
      </div>
    );
  }

  return (
    <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '20px' }}>
      <div style={{ background: 'white', borderRadius: '12px', padding: '32px', boxShadow: '0 4px 12px rgba(0,0,0,0.1)', maxWidth: '450px', width: '100%' }}>

        {/* Header */}
        <div style={{ textAlign: 'center', marginBottom: '24px' }}>
          <div style={{ width: '64px', height: '64px', background: 'linear-gradient(135deg, #10b981 0%, #14b8a6 100%)', borderRadius: '16px', display: 'flex', alignItems: 'center', justifyContent: 'center', margin: '0 auto 16px', boxShadow: '0 4px 12px rgba(16, 185, 129, 0.3)' }}>
            <FileEdit size={36} color="white" strokeWidth={2.5} />
          </div>
          <h1 style={{ fontSize: '28px', fontWeight: 'bold', marginBottom: '8px', background: 'linear-gradient(135deg, #059669 0%, #14b8a6 100%)', WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent' }}>
            Create Account
          </h1>
          <p style={{ color: '#6b7280', fontSize: '14px' }}>Register to get your API key</p>
        </div>

        {/* Form */}
        <form onSubmit={handleRegister}>
          {error && (
            <div style={{ background: '#fee2e2', border: '1px solid #fca5a5', borderRadius: '8px', padding: '12px', marginBottom: '16px', color: '#991b1b', fontSize: '14px' }}>
              {error}
            </div>
          )}

          <div style={{ marginBottom: '16px' }}>
            <label style={{ display: 'block', fontSize: '14px', fontWeight: '500', marginBottom: '6px', color: '#1f2937' }}>Name *</label>
            <input
              type="text"
              placeholder="John Doe"
              value={formData.name}
              onChange={(e) => setFormData({ ...formData, name: e.target.value })}
              disabled={loading}
              style={{ width: '100%', padding: '10px 12px', border: '1px solid #d1d5db', borderRadius: '6px', fontSize: '14px' }}
            />
          </div>

          <div style={{ marginBottom: '16px' }}>
            <label style={{ display: 'block', fontSize: '14px', fontWeight: '500', marginBottom: '6px', color: '#1f2937' }}>Email *</label>
            <input
              type="email"
              placeholder="john@example.com"
              value={formData.email}
              onChange={(e) => setFormData({ ...formData, email: e.target.value })}
              disabled={loading}
              style={{ width: '100%', padding: '10px 12px', border: '1px solid #d1d5db', borderRadius: '6px', fontSize: '14px' }}
            />
          </div>

          <div style={{ marginBottom: '20px' }}>
            <label style={{ display: 'block', fontSize: '14px', fontWeight: '500', marginBottom: '6px', color: '#1f2937' }}>Organization (Optional)</label>
            <input
              type="text"
              placeholder="Your Organization"
              value={formData.organization}
              onChange={(e) => setFormData({ ...formData, organization: e.target.value })}
              disabled={loading}
              style={{ width: '100%', padding: '10px 12px', border: '1px solid #d1d5db', borderRadius: '6px', fontSize: '14px' }}
            />
          </div>

          <button
            type="submit"
            disabled={loading}
            style={{ width: '100%', padding: '12px', background: loading ? '#9ca3af' : 'linear-gradient(135deg, #10b981 0%, #14b8a6 100%)', color: 'white', border: 'none', borderRadius: '8px', fontSize: '14px', fontWeight: '600', cursor: loading ? 'not-allowed' : 'pointer', boxShadow: '0 2px 4px rgba(16, 185, 129, 0.3)', marginBottom: '16px' }}
          >
            {loading ? 'Creating account...' : 'Create Account'}
          </button>

          <div style={{ textAlign: 'center', fontSize: '14px', color: '#6b7280' }}>
            Already have an account?{' '}
            <button
              type="button"
              onClick={() => navigate('/login')}
              style={{ color: '#059669', background: 'none', border: 'none', textDecoration: 'none', fontWeight: '600', cursor: 'pointer' }}
            >
              Sign in
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
