export default function TestApp() {
  return (
    <div style={{
      padding: '40px',
      textAlign: 'center',
      background: 'white',
      margin: '20px',
      borderRadius: '10px',
      boxShadow: '0 2px 8px rgba(0,0,0,0.1)'
    }}>
      <h1 style={{ color: '#10b981', fontSize: '32px', marginBottom: '10px' }}>
        ✅ React is Working!
      </h1>
      <p style={{ color: '#6b7280', fontSize: '16px' }}>
        If you see this, React is rendering correctly.
      </p>
      <p style={{ color: '#6b7280', fontSize: '14px', marginTop: '20px' }}>
        The blank screen was likely due to a routing or component error.
      </p>
    </div>
  );
}
