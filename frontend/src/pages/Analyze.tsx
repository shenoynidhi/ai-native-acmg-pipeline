import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Alert, AlertDescription } from '@/components/ui/alert';
import apiClient from '@/lib/api';
import { Upload, FileText, ArrowLeft, Loader2 } from 'lucide-react';

export default function Analyze() {
  const navigate = useNavigate();
  const [mode, setMode] = useState<'solo' | 'trio'>('solo');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  // Form state
  const [probandVcf, setProbandVcf] = useState<File | null>(null);
  const [fatherVcf, setFatherVcf] = useState<File | null>(null);
  const [motherVcf, setMotherVcf] = useState<File | null>(null);
  const [genomeBuild, setGenomeBuild] = useState('GRCh38');
  const [patientId, setPatientId] = useState('');
  const [probandSex, setProbandSex] = useState<'male' | 'female'>('male');
  const [clinicalNotes, setClinicalNotes] = useState('');

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>, type: 'proband' | 'father' | 'mother') => {
    const file = e.target.files?.[0];
    if (file) {
      if (!file.name.endsWith('.vcf') && !file.name.endsWith('.vcf.gz')) {
        setError('Please select a valid VCF file (.vcf or .vcf.gz)');
        return;
      }
      setError('');

      if (type === 'proband') setProbandVcf(file);
      else if (type === 'father') setFatherVcf(file);
      else if (type === 'mother') setMotherVcf(file);
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');

    if (!probandVcf) {
      setError('Please select a VCF file');
      return;
    }

    if (mode === 'trio' && (!fatherVcf || !motherVcf)) {
      setError('Trio mode requires VCF files for proband, father, and mother');
      return;
    }

    setLoading(true);

    try {
      const formData = new FormData();
      formData.append('vcf_file', probandVcf);
      formData.append('genome_build', genomeBuild);
      formData.append('analysis_mode', mode);

      if (patientId) formData.append('patient_id', patientId);
      if (clinicalNotes) formData.append('clinical_notes', clinicalNotes);

      if (mode === 'trio') {
        if (fatherVcf) formData.append('father_vcf', fatherVcf);
        if (motherVcf) formData.append('mother_vcf', motherVcf);
        formData.append('proband_sex', probandSex);
      }

      const response = await apiClient.post('/analyze', formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
      });

      const sessionId = response.data.session_id;
      navigate(`/analysis/${sessionId}`);
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Analysis submission failed');
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-gray-50 dark:bg-gray-900">
      {/* Header */}
      <header className="bg-white dark:bg-gray-800 border-b border-gray-200 dark:border-gray-700">
        <div className="container mx-auto px-4 py-4">
          <div className="flex items-center gap-4">
            <Button variant="ghost" onClick={() => navigate('/dashboard')}>
              <ArrowLeft className="h-4 w-4 mr-2" />
              Back to Dashboard
            </Button>
            <div>
              <h1 className="text-2xl font-bold text-gray-900 dark:text-white">New Analysis</h1>
              <p className="text-sm text-gray-500 dark:text-gray-400">Upload VCF file for ACMG classification</p>
            </div>
          </div>
        </div>
      </header>

      <div className="container mx-auto px-4 py-8 max-w-4xl">
        <Card>
          <CardHeader>
            <CardTitle>Analysis Configuration</CardTitle>
            <CardDescription>
              Choose analysis mode and upload your VCF file(s) for variant classification
            </CardDescription>
          </CardHeader>
          <CardContent>
            <form onSubmit={handleSubmit} className="space-y-6">
              {error && (
                <Alert variant="destructive">
                  <AlertDescription>{error}</AlertDescription>
                </Alert>
              )}

              {/* Analysis Mode */}
              <div className="space-y-2">
                <Label>Analysis Mode</Label>
                <Tabs value={mode} onValueChange={(v) => setMode(v as 'solo' | 'trio')}>
                  <TabsList className="grid w-full grid-cols-2">
                    <TabsTrigger value="solo">Solo Analysis</TabsTrigger>
                    <TabsTrigger value="trio">Trio Analysis</TabsTrigger>
                  </TabsList>
                  <TabsContent value="solo" className="mt-4">
                    <p className="text-sm text-muted-foreground">
                      Analyze a single proband VCF file without parental information
                    </p>
                  </TabsContent>
                  <TabsContent value="trio" className="mt-4">
                    <p className="text-sm text-muted-foreground">
                      Analyze proband with father and mother VCF files for de novo and compound heterozygous variant detection
                    </p>
                  </TabsContent>
                </Tabs>
              </div>

              {/* Proband VCF */}
              <div className="space-y-2">
                <Label htmlFor="proband-vcf">
                  {mode === 'trio' ? 'Proband VCF File *' : 'VCF File *'}
                </Label>
                <div className="flex items-center gap-3">
                  <Input
                    id="proband-vcf"
                    type="file"
                    accept=".vcf,.vcf.gz"
                    onChange={(e) => handleFileSelect(e, 'proband')}
                    disabled={loading}
                    className="cursor-pointer"
                  />
                  {probandVcf && (
                    <div className="flex items-center gap-2 text-sm text-green-600">
                      <FileText className="h-4 w-4" />
                      {probandVcf.name}
                    </div>
                  )}
                </div>
              </div>

              {/* Trio Mode: Father and Mother VCF */}
              {mode === 'trio' && (
                <>
                  <div className="space-y-2">
                    <Label htmlFor="father-vcf">Father VCF File *</Label>
                    <div className="flex items-center gap-3">
                      <Input
                        id="father-vcf"
                        type="file"
                        accept=".vcf,.vcf.gz"
                        onChange={(e) => handleFileSelect(e, 'father')}
                        disabled={loading}
                        className="cursor-pointer"
                      />
                      {fatherVcf && (
                        <div className="flex items-center gap-2 text-sm text-green-600">
                          <FileText className="h-4 w-4" />
                          {fatherVcf.name}
                        </div>
                      )}
                    </div>
                  </div>

                  <div className="space-y-2">
                    <Label htmlFor="mother-vcf">Mother VCF File *</Label>
                    <div className="flex items-center gap-3">
                      <Input
                        id="mother-vcf"
                        type="file"
                        accept=".vcf,.vcf.gz"
                        onChange={(e) => handleFileSelect(e, 'mother')}
                        disabled={loading}
                        className="cursor-pointer"
                      />
                      {motherVcf && (
                        <div className="flex items-center gap-2 text-sm text-green-600">
                          <FileText className="h-4 w-4" />
                          {motherVcf.name}
                        </div>
                      )}
                    </div>
                  </div>

                  <div className="space-y-2">
                    <Label>Proband Sex *</Label>
                    <Select value={probandSex} onValueChange={(v) => setProbandSex(v as 'male' | 'female')}>
                      <SelectTrigger>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="male">Male</SelectItem>
                        <SelectItem value="female">Female</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                </>
              )}

              {/* Genome Build */}
              <div className="space-y-2">
                <Label>Genome Build</Label>
                <Select value={genomeBuild} onValueChange={setGenomeBuild}>
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="GRCh38">GRCh38 (hg38)</SelectItem>
                    <SelectItem value="GRCh37">GRCh37 (hg19)</SelectItem>
                  </SelectContent>
                </Select>
              </div>

              {/* Patient ID */}
              <div className="space-y-2">
                <Label htmlFor="patient-id">Patient ID (Optional)</Label>
                <Input
                  id="patient-id"
                  type="text"
                  placeholder="e.g., PATIENT001"
                  value={patientId}
                  onChange={(e) => setPatientId(e.target.value)}
                  disabled={loading}
                />
              </div>

              {/* Clinical Notes */}
              <div className="space-y-2">
                <Label htmlFor="clinical-notes">Clinical Notes (Optional)</Label>
                <Textarea
                  id="clinical-notes"
                  placeholder="Enter any relevant clinical information, phenotypes, or notes..."
                  rows={4}
                  value={clinicalNotes}
                  onChange={(e) => setClinicalNotes(e.target.value)}
                  disabled={loading}
                />
              </div>

              {/* Submit Button */}
              <div className="flex items-center justify-end gap-3 pt-4">
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => navigate('/dashboard')}
                  disabled={loading}
                >
                  Cancel
                </Button>
                <Button type="submit" disabled={loading}>
                  {loading ? (
                    <>
                      <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                      Uploading...
                    </>
                  ) : (
                    <>
                      <Upload className="h-4 w-4 mr-2" />
                      Start Analysis
                    </>
                  )}
                </Button>
              </div>
            </form>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
