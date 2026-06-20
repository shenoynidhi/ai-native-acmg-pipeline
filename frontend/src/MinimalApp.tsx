export default function MinimalApp() {
  return (
    <div style={{
      minHeight: '100vh',
      background: 'linear-gradient(135deg, #fffbeb 0%, #d1fae5 100%)',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      padding: '20px'
    }}>
      <div style={{
        background: 'white',
        padding: '40px',
        borderRadius: '12px',
        boxShadow: '0 4px 12px rgba(0,0,0,0.1)',
        maxWidth: '500px',
        width: '100%',
        textAlign: 'center'
      }}>
        <div style={{
          width: '60px',
          height: '60px',
          background: 'linear-gradient(135deg, #10b981 0%, #14b8a6 100%)',
          borderRadius: '16px',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          margin: '0 auto 20px',
          boxShadow: '0 4px 8px rgba(16, 185, 129, 0.3)'
        }}>
          <span style={{ fontSize: '32px' }}>🤖</span>
        </div>

        <h1 style={{
          background: 'linear-gradient(135deg, #059669 0%, #14b8a6 100%)',
          WebkitBackgroundClip: 'text',
          WebkitTextFillColor: 'transparent',
          fontSize: '28px',
          fontWeight: 'bold',
          marginBottom: '10px'
        }}>
          ACMG Assistant
        </h1>

        <p style={{
          color: '#6b7280',
          fontSize: '14px',
          marginBottom: '30px'
        }}>
          AI-Powered Variant Analysis Platform
        </p>

        <div style={{
          background: '#f0fdf4',
          border: '1px solid #bbf7d0',
          borderRadius: '8px',
          padding: '20px',
          marginBottom: '20px',
          textAlign: 'left'
        }}>
          <h3 style={{ color: '#059669', fontSize: '16px', marginBottom: '10px' }}>
            ✅ Frontend is Working!
          </h3>
          <p style={{ color: '#6b7280', fontSize: '14px', lineHeight: '1.6' }}>
            The React application is now running successfully. The Tailwind CSS error has been bypassed.
          </p>
        </div>

        <button style={{
          background: 'linear-gradient(135deg, #10b981 0%, #14b8a6 100%)',
          color: 'white',
          border: 'none',
          padding: '12px 24px',
          borderRadius: '8px',
          fontSize: '14px',
          fontWeight: '600',
          cursor: 'pointer',
          boxShadow: '0 2px 4px rgba(16, 185, 129, 0.3)',
          width: '100%'
        }}>
          Continue to Login
        </button>

        <p style={{
          marginTop: '20px',
          fontSize: '12px',
          color: '#9ca3af'
        }}>
          Next: We'll add the full UI components
        </p>
      </div>
    </div>
  );
}
