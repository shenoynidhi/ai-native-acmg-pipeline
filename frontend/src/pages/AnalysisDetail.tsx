import { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Progress } from '@/components/ui/progress';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Alert, AlertDescription } from '@/components/ui/alert';
import apiClient, { getApiKey } from '@/lib/api';
import type { Session, ProgressEvent } from '@/types';
import {
  ArrowLeft,
  Download,
  CheckCircle,
  XCircle,
  Clock,
  Activity,
  FileText,
  AlertCircle,
} from 'lucide-react';

export default function AnalysisDetail() {
  const { sessionId } = useParams<{ sessionId: string }>();
  const navigate = useNavigate();
  const [progressEvents, setProgressEvents] = useState<ProgressEvent[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);

  const { data: session, refetch } = useQuery<Session>({
    queryKey: ['session', sessionId],
    queryFn: async () => {
      const response = await apiClient.get(`/status/${sessionId}`);
      return response.data;
    },
    refetchInterval: (data) => {
      return data?.status === 'running' || data?.status === 'queued' ? 2000 : false;
    },
  });

  useEffect(() => {
    if (!sessionId || !session) return;
    if (session.status !== 'running' && session.status !== 'queued') return;

    const apiKey = getApiKey();
    if (!apiKey) return;

    const eventSource = new EventSource(
      `${import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'}/stream/${sessionId}?api_key=${apiKey}`
    );

    setIsStreaming(true);

    eventSource.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        setProgressEvents((prev) => [...prev, data]);

        if (data.stage === 'complete' || data.stage === 'failed') {
          refetch();
          eventSource.close();
          setIsStreaming(false);
        }
      } catch (err) {
        console.error('SSE parse error:', err);
      }
    };

    eventSource.onerror = () => {
      eventSource.close();
      setIsStreaming(false);
    };

    return () => {
      eventSource.close();
      setIsStreaming(false);
    };
  }, [sessionId, session?.status, refetch]);

  const getStatusIcon = (status: string) => {
    switch (status) {
      case 'complete':
        return <CheckCircle className="h-5 w-5 text-green-500" />;
      case 'failed':
        return <XCircle className="h-5 w-5 text-red-500" />;
      case 'running':
        return <Activity className="h-5 w-5 text-blue-500 animate-pulse" />;
      case 'queued':
        return <Clock className="h-5 w-5 text-yellow-500" />;
      default:
        return <AlertCircle className="h-5 w-5 text-gray-500" />;
    }
  };

  const getStatusBadge = (status: string) => {
    const variants: Record<string, 'default' | 'secondary' | 'destructive' | 'outline'> = {
      complete: 'default',
      running: 'secondary',
      queued: 'outline',
      failed: 'destructive',
    };

    const colors: Record<string, string> = {
      complete: 'bg-green-500 hover:bg-green-600',
      running: 'bg-blue-500 hover:bg-blue-600',
      queued: 'bg-yellow-500 hover:bg-yellow-600',
      failed: 'bg-red-500 hover:bg-red-600',
    };

    return (
      <Badge variant={variants[status] || 'default'} className={colors[status]}>
        {status.toUpperCase()}
      </Badge>
    );
  };

  const getClassificationBadge = (classification: string) => {
    const colors: Record<string, string> = {
      P: 'bg-red-500 text-white hover:bg-red-600',
      LP: 'bg-orange-500 text-white hover:bg-orange-600',
      VUS: 'bg-yellow-500 text-white hover:bg-yellow-600',
      LB: 'bg-lime-500 text-white hover:bg-lime-600',
      B: 'bg-green-500 text-white hover:bg-green-600',
    };

    const labels: Record<string, string> = {
      P: 'Pathogenic',
      LP: 'Likely Pathogenic',
      VUS: 'VUS',
      LB: 'Likely Benign',
      B: 'Benign',
    };

    return (
      <Badge className={colors[classification] || 'bg-gray-500'}>
        {labels[classification] || classification}
      </Badge>
    );
  };

  const handleDownload = async (format: 'xlsx' | 'tsv' | 'html') => {
    try {
      const response = await apiClient.get(`/download/${sessionId}/${format}`, {
        responseType: 'blob',
      });

      const url = window.URL.createObjectURL(new Blob([response.data]));
      const link = document.createElement('a');
      link.href = url;
      link.setAttribute('download', `analysis_${sessionId}.${format}`);
      document.body.appendChild(link);
      link.click();
      link.remove();
    } catch (err) {
      console.error('Download failed:', err);
    }
  };

  if (!session) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <div className="text-center">
          <Activity className="h-12 w-12 text-blue-500 animate-spin mx-auto mb-4" />
          <p className="text-lg font-medium">Loading analysis...</p>
        </div>
      </div>
    );
  }

  const isRunning = session.status === 'running' || session.status === 'queued';
  const isComplete = session.status === 'complete';
  const isFailed = session.status === 'failed';

  return (
    <div className="min-h-screen bg-gray-50 dark:bg-gray-900">
      {/* Header */}
      <header className="bg-white dark:bg-gray-800 border-b border-gray-200 dark:border-gray-700">
        <div className="container mx-auto px-4 py-4">
          <div className="flex items-center gap-4">
            <Button variant="ghost" onClick={() => navigate('/dashboard')}>
              <ArrowLeft className="h-4 w-4 mr-2" />
              Back
            </Button>
            <div className="flex-1">
              <div className="flex items-center gap-3">
                <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Analysis Details</h1>
                {getStatusIcon(session.status)}
                {getStatusBadge(session.status)}
              </div>
              <p className="text-sm text-gray-500 dark:text-gray-400">
                Session ID: {sessionId}
              </p>
            </div>
          </div>
        </div>
      </header>

      <div className="container mx-auto px-4 py-8">
        {/* Progress Section (Running/Queued) */}
        {isRunning && (
          <Card className="mb-6">
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Activity className="h-5 w-5 animate-pulse" />
                Analysis in Progress
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="space-y-2">
                <div className="flex items-center justify-between text-sm">
                  <span className="font-medium">Progress</span>
                  <span className="text-muted-foreground">{session.progress_pct}%</span>
                </div>
                <Progress value={session.progress_pct} className="h-2" />
              </div>

              {progressEvents.length > 0 && (
                <div className="space-y-2">
                  <h3 className="text-sm font-medium">Live Updates:</h3>
                  <div className="max-h-[300px] overflow-y-auto bg-gray-50 dark:bg-gray-900 rounded-lg p-4 space-y-2">
                    {progressEvents.slice(-20).map((event, idx) => (
                      <div key={idx} className="text-xs font-mono">
                        <span className="text-muted-foreground">[{event.timestamp}]</span>{' '}
                        <span className="font-semibold text-blue-600 dark:text-blue-400">
                          {event.stage}
                        </span>
                        {': '}
                        <span>{event.message}</span>
                        {event.variant_id && (
                          <span className="text-green-600 dark:text-green-400"> ({event.variant_id})</span>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </CardContent>
          </Card>
        )}

        {/* Error Section (Failed) */}
        {isFailed && session.error_message && (
          <Alert variant="destructive" className="mb-6">
            <AlertCircle className="h-4 w-4" />
            <AlertDescription>
              <strong>Analysis Failed:</strong> {session.error_message}
            </AlertDescription>
          </Alert>
        )}

        {/* Summary Cards */}
        <div className="grid grid-cols-1 md:grid-cols-4 gap-6 mb-6">
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-sm font-medium">Analysis Mode</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">{session.analysis_mode.toUpperCase()}</div>
              <p className="text-xs text-muted-foreground mt-1">{session.genome_build}</p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-sm font-medium">Total Variants</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">{session.variant_count || 0}</div>
              <p className="text-xs text-muted-foreground mt-1">Classified</p>
            </CardContent>
          </Card>

          {session.denovo_count !== undefined && (
            <Card>
              <CardHeader className="pb-3">
                <CardTitle className="text-sm font-medium">De Novo</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="text-2xl font-bold">{session.denovo_count}</div>
                <p className="text-xs text-muted-foreground mt-1">Variants</p>
              </CardContent>
            </Card>
          )}

          {session.compound_het_count !== undefined && (
            <Card>
              <CardHeader className="pb-3">
                <CardTitle className="text-sm font-medium">Compound Het</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="text-2xl font-bold">{session.compound_het_count}</div>
                <p className="text-xs text-muted-foreground mt-1">Variants</p>
              </CardContent>
            </Card>
          )}
        </div>

        {/* Classification Distribution */}
        {isComplete && session.classifications && (
          <Card className="mb-6">
            <CardHeader>
              <CardTitle>Classification Distribution</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="flex items-center gap-6 flex-wrap">
                {Object.entries(session.classifications).map(([key, value]) => (
                  <div key={key} className="flex items-center gap-3">
                    {getClassificationBadge(key)}
                    <span className="text-2xl font-bold">{value}</span>
                    <span className="text-sm text-muted-foreground">variants</span>
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>
        )}

        {/* Actions */}
        {isComplete && (
          <Card className="mb-6">
            <CardHeader>
              <CardTitle>Download Results</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="flex flex-wrap gap-3">
                <Button onClick={() => handleDownload('xlsx')}>
                  <Download className="h-4 w-4 mr-2" />
                  Download XLSX
                </Button>
                <Button variant="outline" onClick={() => handleDownload('tsv')}>
                  <Download className="h-4 w-4 mr-2" />
                  Download TSV
                </Button>
                <Button variant="outline" onClick={() => handleDownload('html')}>
                  <Download className="h-4 w-4 mr-2" />
                  Download HTML Report
                </Button>
                <Button variant="secondary" onClick={() => navigate(`/qc/${sessionId}`)}>
                  <FileText className="h-4 w-4 mr-2" />
                  View QC Results
                </Button>
              </div>
            </CardContent>
          </Card>
        )}

        {/* Placeholder for Variants Table */}
        {isComplete && (
          <Card>
            <CardHeader>
              <CardTitle>Classified Variants</CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-sm text-muted-foreground mb-4">
                Variants table will be displayed here. For now, please download the results using the buttons above.
              </p>
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  );
}
