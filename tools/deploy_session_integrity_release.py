"""Apply the tested session update after the user's explicit restart approval."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tools import deploy_zip_frontend_release as deploy
deploy.REPORT=deploy.ROOT/'reports/strict-session-integrity-20261103'
deploy.RELEASE=deploy.ROOT/'releases/StrictRecords-SessionIntegrity-20261103/Windows/StrictRecords'
deploy.BACKEND_STOP_PID=int(sys.argv[sys.argv.index('--backend-pid')+1])
if __name__=='__main__':deploy.main()
