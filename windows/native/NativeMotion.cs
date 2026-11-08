using System;
using System.Collections.Generic;
using System.Windows.Media.Media3D;

namespace StrictBotNative {
public sealed class Foot {
    public Point3D Ground,Start,Target;
    public bool Planted=true;
    public double Lift,Roll,Yaw=65;
}
public sealed class Motion {
    public static readonly string[] Stages={"entry","walking","slow_down","stop","turn","face_front","point","speak","idle"};
    public readonly List<string> Visited=new List<string>();
    public readonly Foot[] Feet={new Foot(),new Foot()};
    public string Stage="entry";
    public double X,TargetX,Velocity,Time,StageTime,Phase,Speed=1,Gesture,Turn;
    public double PelvisYaw=65,TorsoYaw=65,HeadYaw=65,Bob,Sway;
    Point3D[] settle;double[] settleLift;
    int settleFirst;
    double startX;
    readonly double travelDuration,distance;
    double travelTime,idleGestureFrom=1;
    double effectiveSpeed=1;
    public bool Debug;
    public bool SpeechHold;
    public Motion(double start,double target,bool walk=true) {
        X=start;TargetX=target;
        startX=start;distance=Math.Max(0,target-start);travelDuration=Math.Max(1.0,1.875*distance/1.35);
        if(!walk){X=target;Stage="stop";}
        for(int i=0;i<2;i++){double side=i==0?-1:1;Feet[i].Ground=new Point3D(X+(i==0?.22:-.20),0,-side*.23);}
        Visited.Add(Stage);
    }
    public static double Clamp(double p,double low=0,double high=1){return Math.Max(low,Math.Min(high,p));}
    public static double Ease(double p){p=Clamp(p);return p*p*p*(10+p*(-15+6*p));}
    public static double Mix(double a,double b,double p){return a+(b-a)*p;}
    public static Point3D Mix(Point3D a,Point3D b,double p){return a+(b-a)*p;}
    public void Change(string stage) {
        if(stage==Stage)return;
        Stage=stage;StageTime=0;Visited.Add(stage);
        if(stage=="idle")idleGestureFrom=Gesture;
        if(stage=="stop") {
            settle=new Point3D[]{Feet[0].Ground,Feet[1].Ground};
            settleLift=new double[]{Feet[0].Lift,Feet[1].Lift};
            settleFirst=Feet[0].Lift>Feet[1].Lift?0:1;
        }
    }
    public void Update(double dt) {
        if(Debug)return;
        double left=Math.Min(.25,Math.Max(0,dt));
        while(left>.000001){double step=Math.Min(left,1.0/120);effectiveSpeed=Mix(effectiveSpeed,Speed,1-Math.Exp(-step*8));Step(step*effectiveSpeed);left-=step;}
    }
    public void Reanchor(double target) {
        double delta=target-TargetX;X+=delta;TargetX=target;startX+=delta;
        foreach(var foot in Feet){foot.Ground+=new Vector3D(delta,0,0);foot.Start+=new Vector3D(delta,0,0);foot.Target+=new Vector3D(delta,0,0);}
        if(settle!=null)for(int i=0;i<2;i++)settle[i]+=new Vector3D(delta,0,0);
    }
    void Step(double dt) {
        Time+=dt;StageTime+=dt;
        bool moving=Array.IndexOf(Stages,Stage)<3;
        if(moving) {
            travelTime+=dt;double progress=Clamp(travelTime/travelDuration);
            double next=startX+distance*Ease(progress),dx=Math.Max(0,next-X);
            Velocity=distance/travelDuration*30*progress*progress*(1-progress)*(1-progress);
            X=next;Phase+=dx/.84;
            for(int i=0;i<2;i++) {
                Foot foot=Feet[i];double phase=(Phase+i*.5)%1;
                if(phase>=.60) {
                    if(foot.Planted){foot.Start=foot.Ground;foot.Target=new Point3D(Math.Min(X+.56,TargetX+.12),0,i==0?.23:-.23);}
                    foot.Planted=false;double p=(phase-.60)/.4;
                    foot.Ground=Mix(foot.Start,foot.Target,Ease(p));
                    double lift=Math.Pow(Math.Sin(Math.PI*p),2);
                    foot.Lift=lift*.17;
                    foot.Roll=Mix(-16,12,p)*lift;
                } else {
                    if(!foot.Planted)foot.Ground=foot.Target;
                    foot.Lift=0;foot.Roll=0;foot.Planted=true;
                }
            }
            if(progress>=1){X=TargetX;Velocity=0;Change("stop");}
            else if(progress>.65 && X>-.6)Change("slow_down");
            else if(X>-.6)Change("walking");
        } else if(Stage=="stop") {
            if(settle==null){settle=new[]{Feet[0].Ground,Feet[1].Ground};settleLift=new[]{Feet[0].Lift,Feet[1].Lift};}
            for(int i=0;i<2;i++) {
                double side=i==0?-1:1,raw=Clamp((StageTime-(i==settleFirst?0:.28))/.28),p=Ease(raw);
                Feet[i].Ground=Mix(settle[i],new Point3D(X+side*.08,0,-side*.23),p);
                Feet[i].Lift=settleLift[i]*(1-p)+Math.Pow(Math.Sin(Math.PI*raw),2)*.055;Feet[i].Roll=0;Feet[i].Planted=raw==0&&settleLift[i]==0||raw>=1;
            }
            if(StageTime>=.55)Change("turn");
        } else if(Stage=="turn") {
            Turn=Ease(StageTime/.95);
            for(int i=0;i<2;i++) {
                double side=i==0?-1:1,p=Clamp((StageTime-i*.45)/.45);
                Feet[i].Ground=Mix(new Point3D(X+side*.08,0,-side*.23),new Point3D(X+side*.24,0,0),Ease(p));
                Feet[i].Lift=Math.Pow(Math.Sin(Math.PI*p),2)*.055;Feet[i].Yaw=65*(1-Ease(p));Feet[i].Planted=p==0||p==1;
            }
            if(StageTime>=.95)Change("face_front");
        } else if(Stage=="face_front" && StageTime>=.35)Change("point");
        else if(Stage=="point"){Gesture=Ease(StageTime/.7);if(StageTime>=.75)Change("speak");}
        else if(Stage=="speak"){double blend=Ease(StageTime/.35);Gesture=1-.04*blend+.025*Math.Sin(StageTime*3.2)*blend;if(StageTime>=2.2&&!SpeechHold)Change("idle");}
        else if(Stage=="idle")Gesture=idleGestureFrom*(1-Ease(StageTime/1.5));
        if(Stage=="turn") {
            PelvisYaw=65*(1-Ease(StageTime/.95));TorsoYaw=65*(1-Ease((StageTime-.06)/.78));
            HeadYaw=65*(1-Ease((StageTime+.08)/.65));
        } else if(Array.IndexOf(Stages,Stage)>4){PelvisYaw=TorsoYaw=HeadYaw=0;Turn=1;}
        double gait=moving?Velocity/1.35:0;
        double targetBob=moving?(-.035-.015*Math.Abs(Math.Sin(Phase*Math.PI*2)))*gait:.006*Math.Sin(Time*2.1);
        double targetSway=moving?.014*Math.Sin(Phase*Math.PI*2)*gait:.003*Math.Sin(Time*1.4);
        Sway=Mix(Sway,targetSway,1-Math.Exp(-dt*10));
        double reachLimit=.10;
        for(int i=0;i<2;i++) {
            var hip=Rotate(new Point3D(i==0?-.24:.24,0,0),PelvisYaw);var ankle=Ankle(i);
            double dx=ankle.X-hip.X-Sway,dz=ankle.Z-hip.Z;
            double maximumY=ankle.Y+Math.Sqrt(Math.Max(.01,1.295*1.295-dx*dx-dz*dz));
            reachLimit=Math.Min(reachLimit,maximumY-1.47);
        }
        targetBob=Math.Min(targetBob,reachLimit-.008);
        Bob=Math.Min(reachLimit,Mix(Bob,targetBob,1-Math.Exp(-dt*12)));
    }
    public void SetStage(string stage) {
        if(Array.IndexOf(Stages,stage)<0)throw new ArgumentException("Unknown animation stage");
        for(int i=0;i<12000 && Stage!=stage;i++)Update(1.0/120);
        int samples=stage=="point"?70:stage=="speak"?90:stage=="turn"?55:stage=="idle"?220:25;
        for(int i=0;i<samples;i++)Update(1.0/120);
        Debug=true;
    }
    public static Point3D Rotate(Point3D p,double angle) {
        double a=angle*Math.PI/180;return new Point3D(p.X*Math.Cos(a)+p.Z*Math.Sin(a),p.Y,-p.X*Math.Sin(a)+p.Z*Math.Cos(a));
    }
    public Point3D Ankle(int i){var f=Feet[i];return new Point3D(f.Ground.X-X,.21+f.Lift,f.Ground.Z);}
    public Point3D Hip(int i){var p=Rotate(new Point3D(i==0?-.24:.24,1.47,0),PelvisYaw);p.X+=Sway;p.Y+=Bob;return p;}
    public Point3D Knee(int i) {
        var hip=Hip(i);var ankle=Ankle(i);Vector3D axis=ankle-hip;double actual=axis.Length;axis.Normalize();
        const double a=.65,b=.65;double distance=Math.Min(actual,a+b-.00001);
        double along=(a*a-b*b+distance*distance)/(2*distance),height=Math.Sqrt(Math.Max(0,a*a-along*along));
        Point3D forward=Rotate(new Point3D(0,0,1),PelvisYaw);
        Vector3D bend=new Vector3D(forward.X,forward.Y,forward.Z);bend-=axis*Vector3D.DotProduct(bend,axis);bend.Normalize();
        return hip+axis*along+bend*height;
    }
}
}
