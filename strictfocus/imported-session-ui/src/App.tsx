/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 *
 * StrictRecords — Strict Session Desktop Frontend
 * Modular, production-integration-ready UI with external state providers,
 * 50:50 restricted split browser, draggable/resizable camera PIP, and expandable
 * monitoring telemetry.
 */

import React, { useState } from 'react';
import { SplitBrowserWorkspace } from './components/browser/SplitBrowserWorkspace';
import { FloatingCameraPip } from './components/camera/FloatingCameraPip';
import { SessionFooter } from './components/footer/SessionFooter';
import { CompactHeader } from './components/header/CompactHeader';
import { IntegrationDocsModal } from './components/integration/IntegrationDocsModal';
import { EndSessionModal } from './components/modals/EndSessionModal';
import { MonitoringBar } from './components/monitoring/MonitoringBar';
import { MonitoringDetailsPanel } from './components/monitoring/MonitoringDetailsPanel';
import { StrictSessionProvider } from './providers/StrictSessionContext';
import { EnvironmentMode } from './types/strictSession';
import {DesktopRuntimeProvider,isDesktopHost,useDesktopRuntime} from './services/DesktopRuntime';

function getInitialEnvironmentMode(): EnvironmentMode {
  if(isDesktopHost)return 'PRODUCTION';
  if (typeof window !== 'undefined') {
    const params = new URLSearchParams(window.location.search);
    if (params.get('mode')?.toUpperCase() === 'PRODUCTION') {
      return 'PRODUCTION';
    }
    // If running inside an actual Windows WebView2 container with ?prod=1
    if (params.get('env')?.toUpperCase() === 'PROD') {
      return 'PRODUCTION';
    }
  }
  return 'DEMO';
}

function SessionApp() {
  const [isIntegrationModalOpen, setIsIntegrationModalOpen] = useState(false);
  const [isEndSessionModalOpen, setIsEndSessionModalOpen] = useState(false);

  return (
    <StrictSessionProvider initialMode={getInitialEnvironmentMode()}>
      <div className="h-screen w-screen overflow-hidden flex flex-col bg-[#080B11] text-[#F1F5F9] select-none">
        {/* 1. Compact Windows Desktop Application Header */}
        <CompactHeader
          onOpenIntegrationModal={() => setIsIntegrationModalOpen(true)}
          onRequestEndSessionModal={() => setIsEndSessionModalOpen(true)}
        />

        {/* 2. External-Provider-Driven Monitoring & Telemetry Strip */}
        <MonitoringBar />

        {/* 3. Expandable Granular Monitoring Details Drawer */}
        <MonitoringDetailsPanel />

        {/* 4. 50:50 Dual-Pane Restricted Browser Workspace + Floating Camera PIP */}
        <SplitBrowserWorkspace>
          <FloatingCameraPip />
        </SplitBrowserWorkspace>

        {/* 5. Bottom Session Policy & End Session Control Bar */}
        <SessionFooter
          onRequestEndSessionModal={() => setIsEndSessionModalOpen(true)}
          onOpenIntegrationModal={() => setIsIntegrationModalOpen(true)}
        />

        {/* 6. Modals for Host Bridge SDK Inspection & Session Termination */}
        {!isDesktopHost && <IntegrationDocsModal
          isOpen={isIntegrationModalOpen}
          onClose={() => setIsIntegrationModalOpen(false)}
        />}

        <EndSessionModal
          isOpen={isEndSessionModalOpen}
          onClose={() => setIsEndSessionModalOpen(false)}
        />
      </div>
    </StrictSessionProvider>
  );
}

export default function App(){return <DesktopRuntimeProvider><SessionApp/></DesktopRuntimeProvider>;}
