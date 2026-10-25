import { Map as MapIcon } from "lucide-react";
import { useStore } from "../lib/store";
import { FusedMap } from "./FusedMap";
import { Badge, Panel } from "./ui";

export function MapPanel() {
  const reg = useStore((s) => s.state?.registration);
  const calib = useStore((s) => s.state?.calibration);
  return (
    <Panel
      title="Fused local scene"
      icon={<MapIcon size={14} />}
      right={
        <>
          {calib && <Badge color={calib.valid ? "var(--good)" : "var(--critical)"}>drone→map {calib.valid ? `±${calib.error_bound_m} m` : "invalid"}</Badge>}
          {reg && <Badge color={reg.valid ? "var(--good)" : "var(--warning)"}>phone→map {reg.valid ? `±${reg.residual_m?.toFixed(2)} m` : "not registered"}</Badge>}
        </>
      }
    >
      <FusedMap />
    </Panel>
  );
}
