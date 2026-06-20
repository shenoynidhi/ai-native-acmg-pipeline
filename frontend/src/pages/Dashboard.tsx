import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Skeleton } from '@/components/ui/skeleton';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import apiClient, { clearApiKey } from '@/lib/api';
import type { DashboardStats, Session } from '@/types';
import { BarChart, FileText, Activity, AlertCircle, Upload, LogOut, Settings } from 'lucide-react';

export default function Dashboard() {
  const navigate = useNavigate();
  const [statusFilter, setStatusFilter] = useState<string>('all');
  const [searchTerm, setSearchTerm] = useState('');

  const { data: stats, isLoading: statsLoading } = useQuery<DashboardStats>({
    queryKey: ['dashboard-stats'],
    queryFn: async () => {
      const response = await apiClient.get('/api/dashboard/stats');
      return response.data;
    },
  });

  const { data: analyses, isLoading: analysesLoading } = useQuery<Session[]>({
    queryKey: ['dashboard-analyses', statusFilter, searchTerm],
    queryFn: async () => {
      const params: any = { limit: 50 };
      if (statusFilter !== 'all') params.status = statusFilter;
      if (searchTerm) params.search = searchTerm;

      const response = await apiClient.get('/api/dashboard/analyses', { params });
      return response.data.analyses || [];
    },
  });

  const handleLogout = () => {
    clearApiKey();
    navigate('/login');
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
      P: 'bg-red-500 text-white',
      LP: 'bg-orange-500 text-white',
      VUS: 'bg-yellow-500 text-white',
      LB: 'bg-lime-500 text-white',
      B: 'bg-green-500 text-white',
    };

    return (
      <Badge className={colors[classification] || 'bg-gray-500'}>
        {classification}
      </Badge>
    );
  };

  const formatDate = (dateStr: string) => {
    return new Date(dateStr).toLocaleDateString('en-US', {
      month: 'short',
      day: 'numeric',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  };

  return (
    <div className="min-h-screen bg-gray-50 dark:bg-gray-900">
      {/* Header */}
      <header className="bg-white dark:bg-gray-800 border-b border-gray-200 dark:border-gray-700">
        <div className="container mx-auto px-4 py-4">
          <div className="flex items-center justify-between">
            <div>
              <h1 className="text-2xl font-bold text-gray-900 dark:text-white">ACMG Pipeline</h1>
              <p className="text-sm text-gray-500 dark:text-gray-400">Variant Classification Dashboard</p>
            </div>
            <div className="flex items-center gap-3">
              <Button variant="outline" onClick={() => navigate('/settings')}>
                <Settings className="h-4 w-4 mr-2" />
                Settings
              </Button>
              <Button variant="outline" onClick={handleLogout}>
                <LogOut className="h-4 w-4 mr-2" />
                Logout
              </Button>
            </div>
          </div>
        </div>
      </header>

      <div className="container mx-auto px-4 py-8">
        {/* Stats Cards */}
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6 mb-8">
          {statsLoading ? (
            Array.from({ length: 4 }).map((_, i) => (
              <Card key={i}>
                <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                  <Skeleton className="h-4 w-24" />
                  <Skeleton className="h-4 w-4" />
                </CardHeader>
                <CardContent>
                  <Skeleton className="h-8 w-16 mb-2" />
                  <Skeleton className="h-3 w-32" />
                </CardContent>
              </Card>
            ))
          ) : (
            <>
              <Card>
                <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                  <CardTitle className="text-sm font-medium">Total Analyses</CardTitle>
                  <BarChart className="h-4 w-4 text-muted-foreground" />
                </CardHeader>
                <CardContent>
                  <div className="text-2xl font-bold">{stats?.total_analyses || 0}</div>
                  <p className="text-xs text-muted-foreground">All time</p>
                </CardContent>
              </Card>

              <Card>
                <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                  <CardTitle className="text-sm font-medium">Completed</CardTitle>
                  <FileText className="h-4 w-4 text-muted-foreground" />
                </CardHeader>
                <CardContent>
                  <div className="text-2xl font-bold">{stats?.completed || 0}</div>
                  <p className="text-xs text-muted-foreground">
                    {stats?.total_analyses
                      ? `${Math.round(((stats.completed || 0) / stats.total_analyses) * 100)}% success rate`
                      : '0% success rate'}
                  </p>
                </CardContent>
              </Card>

              <Card>
                <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                  <CardTitle className="text-sm font-medium">Running</CardTitle>
                  <Activity className="h-4 w-4 text-muted-foreground" />
                </CardHeader>
                <CardContent>
                  <div className="text-2xl font-bold">{stats?.running || 0}</div>
                  <p className="text-xs text-muted-foreground">In progress</p>
                </CardContent>
              </Card>

              <Card>
                <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                  <CardTitle className="text-sm font-medium">Failed</CardTitle>
                  <AlertCircle className="h-4 w-4 text-muted-foreground" />
                </CardHeader>
                <CardContent>
                  <div className="text-2xl font-bold">{stats?.failed || 0}</div>
                  <p className="text-xs text-muted-foreground">Needs review</p>
                </CardContent>
              </Card>
            </>
          )}
        </div>

        {/* Classification Distribution */}
        {stats?.classification_distribution && (
          <Card className="mb-8">
            <CardHeader>
              <CardTitle>Classification Distribution</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="flex items-center gap-6 flex-wrap">
                {Object.entries(stats.classification_distribution).map(([key, value]) => (
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

        {/* Actions Bar */}
        <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-6 mb-6">
          <div className="flex flex-col md:flex-row gap-4 items-start md:items-center justify-between">
            <div className="flex-1 flex flex-col sm:flex-row gap-4 w-full md:w-auto">
              <Input
                placeholder="Search by session ID, patient ID, or filename..."
                value={searchTerm}
                onChange={(e) => setSearchTerm(e.target.value)}
                className="flex-1 min-w-[200px]"
              />
              <Select value={statusFilter} onValueChange={setStatusFilter}>
                <SelectTrigger className="w-full sm:w-[180px]">
                  <SelectValue placeholder="Filter by status" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">All Analyses</SelectItem>
                  <SelectItem value="complete">Complete</SelectItem>
                  <SelectItem value="running">Running</SelectItem>
                  <SelectItem value="queued">Queued</SelectItem>
                  <SelectItem value="failed">Failed</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <Button onClick={() => navigate('/analyze')} className="w-full md:w-auto">
              <Upload className="h-4 w-4 mr-2" />
              New Analysis
            </Button>
          </div>
        </div>

        {/* Analyses Table */}
        <Card>
          <CardHeader>
            <CardTitle>Recent Analyses</CardTitle>
          </CardHeader>
          <CardContent>
            {analysesLoading ? (
              <div className="space-y-3">
                {Array.from({ length: 5 }).map((_, i) => (
                  <Skeleton key={i} className="h-12 w-full" />
                ))}
              </div>
            ) : !analyses || analyses.length === 0 ? (
              <div className="text-center py-12">
                <FileText className="h-12 w-12 text-gray-400 mx-auto mb-4" />
                <p className="text-lg font-medium text-gray-900 dark:text-white mb-2">No analyses yet</p>
                <p className="text-sm text-gray-500 dark:text-gray-400 mb-6">
                  Get started by uploading your first VCF file
                </p>
                <Button onClick={() => navigate('/analyze')}>
                  <Upload className="h-4 w-4 mr-2" />
                  Start New Analysis
                </Button>
              </div>
            ) : (
              <div className="overflow-x-auto">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Session ID</TableHead>
                      <TableHead>Patient ID</TableHead>
                      <TableHead>VCF File</TableHead>
                      <TableHead>Mode</TableHead>
                      <TableHead>Status</TableHead>
                      <TableHead>Variants</TableHead>
                      <TableHead>Created</TableHead>
                      <TableHead>Actions</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {analyses.map((session) => (
                      <TableRow key={session.session_id}>
                        <TableCell className="font-mono text-xs">
                          {session.session_id.slice(0, 8)}...
                        </TableCell>
                        <TableCell>{session.patient_id || '-'}</TableCell>
                        <TableCell className="max-w-[200px] truncate">
                          {session.vcf_filename || '-'}
                        </TableCell>
                        <TableCell>
                          <Badge variant="outline">
                            {session.analysis_mode.toUpperCase()}
                          </Badge>
                        </TableCell>
                        <TableCell>{getStatusBadge(session.status)}</TableCell>
                        <TableCell>
                          {session.variant_count ? (
                            <div className="flex flex-wrap gap-1">
                              {session.classifications && (
                                <>
                                  {session.classifications.P > 0 && (
                                    <span className="text-xs">P:{session.classifications.P}</span>
                                  )}
                                  {session.classifications.LP > 0 && (
                                    <span className="text-xs">LP:{session.classifications.LP}</span>
                                  )}
                                  {session.classifications.VUS > 0 && (
                                    <span className="text-xs">VUS:{session.classifications.VUS}</span>
                                  )}
                                </>
                              )}
                            </div>
                          ) : (
                            '-'
                          )}
                        </TableCell>
                        <TableCell className="text-xs text-muted-foreground">
                          {formatDate(session.created_at)}
                        </TableCell>
                        <TableCell>
                          <Button
                            size="sm"
                            variant="outline"
                            onClick={() => navigate(`/analysis/${session.session_id}`)}
                          >
                            View
                          </Button>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
