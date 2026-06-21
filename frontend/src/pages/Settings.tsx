import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Alert, AlertDescription } from '@/components/ui/alert';
import apiClient, { getApiKey, setApiKey, clearApiKey } from '@/lib/api';
import { ArrowLeft, Key, Loader2, CheckCircle } from 'lucide-react';

export default function Settings() {
  const navigate = useNavigate();
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [showNewKey, setShowNewKey] = useState(false);
  const [newApiKey, setNewApiKey] = useState('');
  const [email, setEmail] = useState('');

  const currentApiKey = getApiKey() || '';
  const maskedKey = currentApiKey ? `${currentApiKey.slice(0, 8)}${'*'.repeat(32)}` : '';

  const handleRegenerateKey = async () => {
    if (!email) {
      setError('Please enter your email address');
      return;
    }

    if (!confirm('Are you sure you want to regenerate your API key? Your current key will be invalidated.')) {
      return;
    }

    setLoading(true);
    setError('');
    setMessage('');

    try {
      const response = await apiClient.post('/regenerate-key', { email });
      const apiKey = response.data.new_api_key;

      setNewApiKey(apiKey);
      setShowNewKey(true);
      setMessage('API key regenerated successfully! Please save your new key.');
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Failed to regenerate API key');
    } finally {
      setLoading(false);
    }
  };

  const handleSaveNewKey = () => {
    setApiKey(newApiKey);
    setShowNewKey(false);
    setMessage('New API key saved successfully!');
    setTimeout(() => {
      navigate('/dashboard');
    }, 2000);
  };

  const handleLogout = () => {
    clearApiKey();
    navigate('/login');
  };

  return (
    <div className="min-h-screen bg-gray-50 dark:bg-gray-900">
      {/* Header */}
      <header className="bg-white dark:bg-gray-800 border-b border-gray-200 dark:border-gray-700">
        <div className="container mx-auto px-4 py-4">
          <div className="flex items-center gap-4 flex-wrap">
            <Button variant="ghost" onClick={() => navigate('/dashboard')}>
              <ArrowLeft className="h-4 w-4" />
              <span className="mobile-hide-text ml-2">Back to Dashboard</span>
            </Button>
            <div>
              <h1 className="text-xl md:text-2xl font-bold text-gray-900 dark:text-white">Settings</h1>
              <p className="text-xs md:text-sm text-gray-500 dark:text-gray-400">Manage your account and preferences</p>
            </div>
          </div>
        </div>
      </header>

      <div className="container mx-auto px-4 py-8 max-w-3xl">
        {message && (
          <Alert className="mb-6 border-green-500 bg-green-50 dark:bg-green-900/20">
            <CheckCircle className="h-4 w-4 text-green-600" />
            <AlertDescription className="text-green-800 dark:text-green-200">{message}</AlertDescription>
          </Alert>
        )}

        {error && (
          <Alert variant="destructive" className="mb-6">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}

        {/* Show New API Key Modal */}
        {showNewKey && (
          <Card className="mb-6 border-2 border-blue-500">
            <CardHeader>
              <CardTitle className="text-blue-600 dark:text-blue-400">New API Key Generated</CardTitle>
              <CardDescription>
                Save this key immediately - it won't be shown again!
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="bg-gray-100 dark:bg-gray-800 p-4 rounded-lg">
                <code className="text-sm font-mono break-all">{newApiKey}</code>
              </div>
              <div className="flex gap-3">
                <Button
                  onClick={() => {
                    navigator.clipboard.writeText(newApiKey);
                    setMessage('API key copied to clipboard!');
                  }}
                  variant="outline"
                >
                  Copy to Clipboard
                </Button>
                <Button onClick={handleSaveNewKey}>
                  Save & Continue
                </Button>
              </div>
            </CardContent>
          </Card>
        )}

        {/* API Key Section */}
        <Card className="mb-6">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Key className="h-5 w-5" />
              API Key
            </CardTitle>
            <CardDescription>
              Your API key is used to authenticate all requests
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="current-key">Current API Key</Label>
              <Input
                id="current-key"
                type="text"
                value={maskedKey}
                disabled
                className="font-mono"
              />
            </div>

            <div className="space-y-4 pt-4 border-t">
              <div>
                <p className="text-sm font-medium mb-4">Regenerate API Key</p>
                <p className="text-xs text-muted-foreground mb-4">
                  Enter your email to regenerate your API key. Your current key will be invalidated.
                </p>
                <div className="space-y-2 mb-4">
                  <Label htmlFor="email">Email Address</Label>
                  <Input
                    id="email"
                    type="email"
                    placeholder="your@email.com"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    disabled={loading}
                  />
                </div>
                <Button
                  variant="destructive"
                  onClick={handleRegenerateKey}
                  disabled={loading || !email}
                >
                  {loading ? (
                    <>
                      <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                      Regenerating...
                    </>
                  ) : (
                    'Regenerate Key'
                  )}
                </Button>
              </div>
            </div>
          </CardContent>
        </Card>

        {/* Account Actions */}
        <Card>
          <CardHeader>
            <CardTitle>Account Actions</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm font-medium">Sign Out</p>
                <p className="text-xs text-muted-foreground">
                  Sign out of your account on this device
                </p>
              </div>
              <Button variant="outline" onClick={handleLogout}>
                Sign Out
              </Button>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
