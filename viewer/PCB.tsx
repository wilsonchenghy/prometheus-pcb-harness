interface PCBProps {
  className?: string;
}

const PCB = ({ className = '' }: PCBProps) => {
  return (
    <div className={`h-full w-full ${className}`}>
      <iframe
        src={import.meta.env.VITE_TSCIRCUIT_URL}
        className="w-full h-full border-0"
        title="TSCircuit PCB Viewer"
        sandbox="allow-scripts allow-same-origin"
      />
    </div>
  );
};

export default PCB;
