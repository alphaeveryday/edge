# super-admin-ui

> 역할/아키텍처는 루트 [README](../../../README.md)·[docs/context.md](../../../docs/context.md)가 SSOT.
> 이 문서는 로컬 실행·범위 경계만 둔다.

운영자 콘솔(EDGE Admin). tenant-console-ui 와 동일 스택(Vite + React 19 + TypeScript, `react-router-dom`).
현재는 **빌드·배포 파이프라인을 위한 최소 셸**이며, 운영자 인증·cross-tenant 관리 화면은 ALPHA-288 에서 구현한다.

## 실행

Node 패키지 매니저는 **pnpm**이다(ADR-0001). Node 워크스페이스 루트는 `src/pnpm-workspace.yaml`.

```bash
pnpm --filter super-admin-ui dev        # http://localhost:5175
pnpm --filter super-admin-ui build      # tsc --noEmit && vite build → dist/
pnpm --filter super-admin-ui typecheck
```
