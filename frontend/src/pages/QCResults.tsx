import { useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Progress } from '@/components/ui/progress';
import apiClient from '@/lib/api';
import type { QCResult } from '@/types';
import { ArrowLeft, Download, CheckCircle, XCircle, AlertTriangle, Loader2 } from 'lucide-react';

export default function QCResults() {
  const { sessionId } = useParams<{ sessionId: string }>();
  const navigate = useNavigate();
  const [validating, setValidating] = useState(false);

  const { data: qcResult, isLoading, refetch } = useQuery<QCResult>({
    queryKey: ['qc-result', sessionId],
    queryFn: async () => {
      const response = await apiClient.get(`/qc/result/${sessionId}`);
      return response.data;
    },
    retry: false,
  });

  const handleRunQC = async () => {
    if (!sessionId) return;

    setValidating(true);
    try {
      await apiClient.post('/qc/validate', { session_id: sessionId });
      await refetch();
    } catch (err) {
      console.error('QC validation failed:', err);
    } finally {
      setValidating(false);
    }
  };

  const handleExportQC = async () => {
    try {
      const response = await apiClient.get(`/qc/export/${sessionId}`, {
        responseType: 'blob',
      });

      const url = window.URL.createObjectURL(new Blob([response.data]));
      const link = document.createElement('a');
      link.href = url;
      link.setAttribute('download', `qc_report_${sessionId}.csv`);
      document.body.appendChild(link);
      link.click();
      link.remove();
    } catch (err) {
      console.error('Export failed:', err);
    }
  };

  const getStatusIcon = (status: string) => {
    switch (status) {
      case 'PASS':
        return <CheckCircle className="h-6 w-6 text-green-500" />;
      case 'WARNING':
        return <AlertTriangle className="h-6 w-6 text-yellow-500" />;
      case 'FAIL':
        return <XCircle className="h-6 w-6 text-red-500" />;
      default:
        return null;
    }
  };

  const getStatusBadge = (status: string) => {
    const colors: Record<string, string> = {
      PASS: 'bg-green-500 hover:bg-green-600',
      WARNING: 'bg-yellow-500 hover:bg-yellow-600',
      FAIL: 'bg-red-500 hover:bg-red-600',
    };

    return (
      <Badge className={`${colors[status] || 'bg-gray-500'} text-white`}>
        {status}
      </Badge>
    );
  };

  const getCategoryIcon = (status: string) => {
    switch (status) {
      case 'PASS':
        return <CheckCircle className="h-5 w-5 text-green-500" />;
      case 'WARNING':
        return <AlertTriangle className="h-5 w-5 text-yellow-500" />;
      case 'FAIL':
        return <XCircle className="h-5 w-5 text-red-500" />;
      default:
        return null;
    }
  };

  if (isLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <div className="text-center">
          <Loader2 className="h-12 w-12 text-blue-500 animate-spin mx-auto mb-4" />
          <p className="text-lg font-medium">Loading QC results...</p>
        </div>
      </div>
    );
  }

  if (!qcResult && !isLoading) {
    return (
      <div className="min-h-screen bg-gray-50 dark:bg-gray-900">
        <header className="bg-white dark:bg-gray-800 border-b border-gray-200 dark:border-gray-700">
          <div className="container mx-auto px-4 py-4">
            <div className="flex items-center gap-4">
              <Button variant="ghost" onClick={() => navigate(`/analysis/${sessionId}`)}>
                <ArrowLeft className="h-4 w-4 mr-2" />
                Back
              </Button>
              <div>
                <h1 className="text-2xl font-bold text-gray-900 dark:text-white">QC Validation</h1>
              </div>
            </div>
          </div>
        </header>

        <div className="container mx-auto px-4 py-8 max-w-4xl">
          <Card>
            <CardContent className="pt-6 text-center">
              <AlertTriangle className="h-12 w-12 text-yellow-500 mx-auto mb-4" />
              <h2 className="text-xl font-bold mb-2">No QC Results Found</h2>
              <p className="text-muted-foreground mb-6">
                This analysis hasn't been validated yet. Click below to run QC validation.
              </p>
              <Button onClick={handleRunQC} disabled={validating}>
                {validating ? (
                  <>
                    <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                    Running QC Validation...
                  </>
                ) : (
                  'Run QC Validation'
                )}
              </Button>
            </CardContent>
          </Card>
        </div>
      </div>
    );
  }

  if (!qcResult) {
    return null;
  }

  const qcScoreColor = qcResult.qc_score >= 0.9 ? 'bg-green-500' : qcResult.qc_score >= 0.7 ? 'bg-yellow-500' : 'bg-red-500';

  return (
    <div className="min-h-screen bg-gray-50 dark:bg-gray-900">
      {/* Header */}
      <header className="bg-white dark:bg-gray-800 border-b border-gray-200 dark:border-gray-700">
        <div className="container mx-auto px-4 py-4">
          <div className="flex items-center gap-4 flex-wrap">
            <Button variant="ghost" onClick={() => navigate(`/analysis/${sessionId}`)}>
              <ArrowLeft className="h-4 w-4" />
              <span className="mobile-hide-text ml-2">Back to Analysis</span>
            </Button>
            <div className="flex-1 min-w-0">
              <h1 className="text-xl md:text-2xl font-bold text-gray-900 dark:text-white">QC Validation Results</h1>
              <p className="text-xs md:text-sm text-gray-500 dark:text-gray-400 truncate">Quality control report for {sessionId}</p>
            </div>
          </div>
        </div>
      </header>

      <div className="container mx-auto px-4 py-8 max-w-4xl">
        {/* Overall QC Status */}
        <Card className="mb-6">
          <CardHeader>
            <CardTitle className="flex items-center justify-between">
              <span className="flex items-center gap-2">
                {getStatusIcon(qcResult.qc_status)}
                Overall QC Status
              </span>
              {getStatusBadge(qcResult.qc_status)}
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-6">
            {/* QC Score */}
            <div>
              <div className="flex items-center justify-between mb-2">
                <span className="text-sm font-medium">QC Score</span>
                <span className="text-2xl font-bold">{(qcResult.qc_score * 100).toFixed(1)}%</span>
              </div>
              <Progress value={qcResult.qc_score * 100} className={`h-3 ${qcScoreColor}`} />
            </div>

            {/* Confidence */}
            <div>
              <div className="flex items-center justify-between mb-2">
                <span className="text-sm font-medium">Confidence</span>
                <span className="text-lg font-semibold">{(qcResult.confidence * 100).toFixed(1)}%</span>
              </div>
              <Progress value={qcResult.confidence * 100} className="h-2" />
            </div>

            {/* Metadata */}
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 pt-4 border-t">
              <div>
                <p className="text-sm text-muted-foreground">Analysis Mode</p>
                <p className="font-medium">{qcResult.analysis_mode.toUpperCase()}</p>
              </div>
              {qcResult.patient_id && (
                <div>
                  <p className="text-sm text-muted-foreground">Patient ID</p>
                  <p className="font-medium">{qcResult.patient_id}</p>
                </div>
              )}
            </div>
          </CardContent>
        </Card>

        {/* QC Categories Breakdown */}
        <Card className="mb-6">
          <CardHeader>
            <CardTitle>QC Category Breakdown</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-4">
              {[
                { label: 'Input QC', value: qcResult.input_qc },
                { label: 'Annotation QC', value: qcResult.annotation_qc },
                { label: 'Evidence QC', value: qcResult.evidence_qc },
                { label: 'Classification QC', value: qcResult.classification_qc },
                { label: 'Report QC', value: qcResult.report_qc },
              ].map((category) => (
                <div key={category.label} className="flex items-center justify-between p-3 bg-gray-50 dark:bg-gray-800 rounded-lg">
                  <span className="font-medium">{category.label}</span>
                  <div className="flex items-center gap-2">
                    {getCategoryIcon(category.value)}
                    {getStatusBadge(category.value)}
                  </div>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>

        {/* Issues */}
        {qcResult.issues && qcResult.issues.length > 0 && (
          <Card className="mb-6">
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <AlertTriangle className="h-5 w-5 text-yellow-500" />
                Issues Found ({qcResult.issues.length})
              </CardTitle>
            </CardHeader>
            <CardContent>
              <ul className="space-y-2">
                {qcResult.issues.map((issue, idx) => (
                  <li key={idx} className="flex items-start gap-2 text-sm">
                    <span className="text-yellow-500 mt-1">•</span>
                    <span>{issue}</span>
                  </li>
                ))}
              </ul>
            </CardContent>
          </Card>
        )}

        {/* Actions */}
        <Card>
          <CardHeader>
            <CardTitle>Actions</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <Button onClick={handleExportQC}>
                <Download className="h-4 w-4 mr-2" />
                Export QC Report (CSV)
              </Button>
              <Button variant="outline" onClick={handleRunQC} disabled={validating}>
                {validating ? (
                  <>
                    <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                    Re-running...
                  </>
                ) : (
                  'Re-run QC Validation'
                )}
              </Button>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
