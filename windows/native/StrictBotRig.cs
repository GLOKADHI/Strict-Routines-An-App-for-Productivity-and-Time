using System;
using System.Collections.Generic;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Media.Media3D;

namespace StrictBotNative {
public sealed class BodyPose {
    public readonly Transform3DGroup Transform=new Transform3DGroup();
    readonly AxisAngleRotation3D yaw=new AxisAngleRotation3D(new Vector3D(0,1,0),0);
    readonly AxisAngleRotation3D tilt;
    readonly TranslateTransform3D position=new TranslateTransform3D();
    public BodyPose(bool foot=false) {
        tilt=new AxisAngleRotation3D(foot?new Vector3D(1,0,0):new Vector3D(0,0,1),0);
        Transform.Children.Add(new RotateTransform3D(tilt));Transform.Children.Add(new RotateTransform3D(yaw));Transform.Children.Add(position);
    }
    public void Set(Point3D point,double rotation,double angle=0) {
        yaw.Angle=rotation;tilt.Angle=angle;position.OffsetX=point.X;position.OffsetY=point.Y;position.OffsetZ=point.Z;
    }
}
public sealed class ArmourBone {
    public readonly Model3DGroup Group;
    readonly double length;
    readonly ScaleTransform3D stretch=new ScaleTransform3D();
    readonly MatrixTransform3D pose=new MatrixTransform3D();
    public ArmourBone(Model3DGroup parent,double size,double radius,bool shin=false) {
        length=size;Group=G.Group(parent);
        var transform=new Transform3DGroup();transform.Children.Add(stretch);transform.Children.Add(pose);Group.Transform=transform;
        G.Ellipsoid(Group,G.P(0,size*.5,0),new Vector3D(radius*.72,size*.53,radius*.66),G.Graphite);
        var shell=G.Group(Group);G.Shell(shell,size*.79,radius,G.Ceramic,.78);
        shell.Transform=new TranslateTransform3D(0,size*.10,.018);
        G.Ring(Group,G.P(0,size*.82,0),radius*.65,.012,G.Silver,new Vector3D(0,1,0));
        G.Ring(Group,G.P(0,size*.15,0),radius*.78,.012,G.Silver,new Vector3D(0,1,0));
        G.Ellipsoid(Group,G.P(0,size*.49,radius*.68),new Vector3D(radius*.65,size*.29,.035),G.Ceramic);
        G.Tube(Group,new[]{G.P(radius*.57,size*.30,radius*.60),G.P(radius*.56,size*.60,radius*.61)},.011,G.Cyan);
        G.Tube(Group,new[]{G.P(-radius*.61,size*.27,radius*.56),G.P(-radius*.59,size*.67,radius*.59)},.006,G.Silver);
        G.Ellipsoid(Group,G.P(0,0,0),new Vector3D(radius*.78,radius*.78,radius*.78),G.Graphite);
        for(int side=-1;side<=1;side+=2) {
            G.Ellipsoid(Group,G.P(side*radius*.70,0,0),new Vector3D(.018,radius*.64,radius*.64),G.Silver);
            G.Ring(Group,G.P(side*radius*.725,0,0),radius*.43,.009,G.Cyan,new Vector3D(1,0,0));
            G.Ellipsoid(Group,G.P(side*radius*.745,0,0),new Vector3D(.014,radius*.25,radius*.25),G.Graphite);
        }
        if(shin) {
            G.Ellipsoid(Group,G.P(0,size*.32,radius*.75),new Vector3D(radius*.65,size*.22,.035),G.Ceramic);
            G.Tube(Group,new[]{G.P(0,size*.23,radius*.92),G.P(0,size*.43,radius*.92)},.008,G.CyanDark);
        }
    }
    public void Place(Point3D from,Point3D to,double yaw=0) {
        Vector3D delta=to-from;
        double scale=delta.Length/length;if(Math.Abs(stretch.ScaleY-scale)>1e-6)stretch.ScaleY=scale;
        Point3D forward=Motion.Rotate(G.P(0,0,1),yaw);
        pose.Matrix=G.FrameMatrix(from,delta,new Vector3D(forward.X,forward.Y,forward.Z));
    }
}
public sealed class Hand {
    public readonly Model3DGroup Group;
    readonly AxisAngleRotation3D[,] joints=new AxisAngleRotation3D[5,3];
    readonly MatrixTransform3D pose=new MatrixTransform3D();
    public Hand(Model3DGroup parent) {
        Group=G.Group(parent);
        Group.Transform=pose;
        G.Ellipsoid(Group,G.P(0,.065,0),new Vector3D(.103,.105,.065),G.Graphite);
        G.Ellipsoid(Group,G.P(0,.058,.039),new Vector3D(.096,.084,.038),G.Ceramic);
        G.Ring(Group,G.P(0,0,0),.067,.012,G.Silver,new Vector3D(0,1,0));
        G.Ring(Group,G.P(0,-.015,0),.061,.008,G.Cyan,new Vector3D(0,1,0));
        for(int finger=0;finger<5;finger++) {
            var chain=G.Group(Group);
            if(finger<4)chain.Transform=new TranslateTransform3D((finger-1.5)*.046,.138,0);
            else {
                var transform=new Transform3DGroup();
                transform.Children.Add(new RotateTransform3D(new AxisAngleRotation3D(new Vector3D(0,0,1),58)));
                transform.Children.Add(new TranslateTransform3D(-.093,.035,0));chain.Transform=transform;
            }
            double factor=finger==3?.8:finger==1?1.08:1;
            for(int joint=0;joint<3;joint++) {
                var pivot=G.Group(chain);
                joints[finger,joint]=new AxisAngleRotation3D(new Vector3D(1,0,0),20);
                pivot.Transform=new RotateTransform3D(joints[finger,joint]);
                double len=(joint==0?.065:joint==1?.052:.041)*factor;
                G.Ellipsoid(pivot,G.P(0,.002,0),new Vector3D(.023,.023,.023),G.Silver);
                G.Ellipsoid(pivot,G.P(0,len*.52,.003),new Vector3D(.022,len*.52,.022),joint==0?G.Ceramic:G.Silver);
                if(joint==0)G.Ellipsoid(pivot,G.P(0,.015,.022),new Vector3D(.011,.008,.003),G.Graphite);
                var next=G.Group(pivot);next.Transform=new TranslateTransform3D(0,len,0);chain=next;
            }
        }
    }
    public void Pose(Point3D wrist,Vector3D direction,double pointing,double time,double yaw) {
        Point3D forward=Motion.Rotate(G.P(0,0,1),yaw);
        pose.Matrix=G.FrameMatrix(wrist,direction,new Vector3D(forward.X,forward.Y,forward.Z));
        for(int i=0;i<5;i++)for(int j=0;j<3;j++)
            joints[i,j].Angle=(i==0?Motion.Mix(19,0,pointing):Motion.Mix(19,62,pointing))+(i==4?12:0)+Math.Sin(time*1.2+i)*1.2;
    }
}
public sealed class Rig {
    public Model3DGroup CopyLogoHead() {
        var model=head.Clone();model.Transform=Transform3D.Identity;return model;
    }
    public readonly Model3DGroup Scene=new Model3DGroup(),Character=new Model3DGroup();
    public readonly Viewport3D Viewport=new Viewport3D();
    readonly Model3DGroup head,torso,pelvis,neck,faceEyes,mouth;
    readonly ArmourBone[] thighs=new ArmourBone[2],shins=new ArmourBone[2],upper=new ArmourBone[2],fore=new ArmourBone[2];
    readonly Model3DGroup[] boots=new Model3DGroup[2],shoulders=new Model3DGroup[2];
    readonly Hand[] hands=new Hand[2];
    public double PresentationYaw;
    readonly BodyPose headPose=new BodyPose(),torsoPose=new BodyPose(),pelvisPose=new BodyPose(),neckPose=new BodyPose();
    readonly BodyPose[] bootPoses={new BodyPose(true),new BodyPose(true)},shoulderPoses={new BodyPose(),new BodyPose()};
    readonly AxisAngleRotation3D presentationRotation=new AxisAngleRotation3D(new Vector3D(0,1,0),0);
    readonly ScaleTransform3D eyeScale=new ScaleTransform3D(1,1,1,0,.055,.48),mouthScale=new ScaleTransform3D(1,1,1,0,-.10,.48);
    double talkBlend,lastPoseTime;
    public Rig() {
        Scene.Children.Add(new AmbientLight(G.C("#3a4655")));
        Scene.Children.Add(new DirectionalLight(G.C("#e9edf4"),new Vector3D(-.65,-1.2,-2)));
        Scene.Children.Add(new DirectionalLight(G.C("#436c8c"),new Vector3D(1,.15,-1)));
        Scene.Children.Add(new DirectionalLight(G.C("#168795"),new Vector3D(-1,-.3,1)));
        Scene.Children.Add(Character);
        Character.Transform=new RotateTransform3D(presentationRotation);
        var shadow=G.Group(Character);
        G.Ellipsoid(shadow,G.P(0,.008,.03),new Vector3D(.68,.008,.43),new DiffuseMaterial(new SolidColorBrush(Color.FromArgb(44,0,5,15))));
        G.Ring(shadow,G.P(0,.01,0),.63,.006,G.CyanDark,new Vector3D(0,1,0));
        for(int i=0;i<2;i++) {
            thighs[i]=new ArmourBone(Character,.65,.18);
            shins[i]=new ArmourBone(Character,.65,.17,true);
            boots[i]=G.Group(Character);
            boots[i].Transform=bootPoses[i].Transform;
            G.Ellipsoid(boots[i],G.P(0,.03,.12),new Vector3D(.205,.09,.33),G.Rubber);
            G.Ellipsoid(boots[i],G.P(0,.09,.14),new Vector3D(.197,.12,.31),G.Silver);
            G.Ellipsoid(boots[i],G.P(0,.12,.19),new Vector3D(.19,.13,.275),G.Ceramic);
            G.Ellipsoid(boots[i],G.P(0,.19,-.035),new Vector3D(.142,.17,.17),G.Graphite);
            G.Ellipsoid(boots[i],G.P(0,.23,-.025),new Vector3D(.132,.12,.155),G.Ceramic);
            G.Tube(boots[i],new[]{G.P(-.14,.06,.33),G.P(-.075,.055,.405),G.P(.075,.055,.405),G.P(.14,.06,.33)},.012,G.Cyan);
            G.Ring(boots[i],G.P(0,.25,-.025),.117,.010,G.Silver,new Vector3D(0,1,0));
            upper[i]=new ArmourBone(Character,.43,.115);
            fore[i]=new ArmourBone(Character,.43,.145);
            hands[i]=new Hand(Character);
            shoulders[i]=G.Group(Character);
            shoulders[i].Transform=shoulderPoses[i].Transform;
            G.Ellipsoid(shoulders[i],G.P(0,0,0),new Vector3D(.185,.185,.185),G.Graphite);
            G.Ellipsoid(shoulders[i],G.P(0,.045,.015),new Vector3D(.205,.16,.195),G.Ceramic);
            G.Ring(shoulders[i],G.P(i==0?-.175:.175,.035,.02),.09,.014,G.Silver,new Vector3D(1,0,0));
            G.Ring(shoulders[i],G.P(i==0?-.188:.188,.035,.02),.07,.009,G.Cyan,new Vector3D(1,0,0));
        }
        pelvis=G.Group(Character);
        pelvis.Transform=pelvisPose.Transform;
        G.Ellipsoid(pelvis,G.P(0,0,0),new Vector3D(.315,.18,.235),G.Graphite);
        for(int side=-1;side<=1;side+=2)G.Ellipsoid(pelvis,G.P(side*.20,.025,.035),new Vector3D(.18,.12,.17),G.Ceramic);
        G.Ellipsoid(pelvis,G.P(0,-.05,.17),new Vector3D(.155,.14,.07),G.Silver);
        G.Tube(pelvis,new[]{G.P(-.12,.075,.22),G.P(0,.05,.24),G.P(.12,.075,.22)},.012,G.CyanDark);
        torso=G.Group(Character);
        torso.Transform=torsoPose.Transform;
        G.Ellipsoid(torso,G.P(0,-.13,0),new Vector3D(.29,.38,.235),G.Graphite);
        for(int i=0;i<4;i++) {
            double y=-.43+i*.075;
            G.Ring(torso,G.P(0,y,0),.222-i*.007,.018,G.Graphite,new Vector3D(0,1,0));
            G.Ellipsoid(torso,G.P(0,y,.18),new Vector3D(.19,.023,.036),G.Silver);
        }
        G.Ellipsoid(torso,G.P(0,.14,.045),new Vector3D(.425,.25,.245),G.Ceramic);
        for(int side=-1;side<=1;side+=2) {
            G.Ellipsoid(torso,G.P(side*.22,.265,.10),new Vector3D(.18,.11,.12),G.Ceramic);
            G.Tube(torso,new[]{G.P(side*.34,.25,.22),G.P(side*.32,.16,.29),G.P(side*.28,.11,.30)},.009,G.Cyan);
            G.Ellipsoid(torso,G.P(side*.30,-.055,.10),new Vector3D(.10,.17,.18),G.Graphite);
        }
        G.Ellipsoid(torso,G.P(0,.10,.273),new Vector3D(.17,.17,.045),G.Graphite);
        G.Ring(torso,G.P(0,.10,.31),.147,.016,G.Silver,new Vector3D(0,0,1));
        G.Ring(torso,G.P(0,.10,.324),.122,.012,G.Cyan,new Vector3D(0,0,1));
        G.Ellipsoid(torso,G.P(0,.10,.323),new Vector3D(.11,.11,.017),G.CyanDark);
        G.Quad(torso,0,.115,.347,.20,.11,G.TextMaterial("SR",G.C("#cfffff")));
        neck=G.Group(Character);
        neck.Transform=neckPose.Transform;
        G.Ellipsoid(neck,G.P(0,0,0),new Vector3D(.12,.15,.12),G.Graphite);
        G.Ring(neck,G.P(0,.035,0),.115,.018,G.Silver,new Vector3D(0,1,0));
        G.Ring(neck,G.P(0,-.05,0),.105,.012,G.CyanDark,new Vector3D(0,1,0));
        head=G.Group(Character);
        head.Transform=headPose.Transform;
        G.Ellipsoid(head,G.P(0,0,0),new Vector3D(.555,.435,.405),G.Silver);
        G.Ellipsoid(head,G.P(0,.035,.007),new Vector3D(.548,.418,.405),G.Ceramic);
        G.Ellipsoid(head,G.P(0,-.015,.205),new Vector3D(.485,.315,.205),G.Graphite);
        BuildVisor(head);
        // Bezel follows the helmet's curved face rather than a flat rectangular sticker.
        var bezel=new List<Point3D>();
        for(int i=0;i<=64;i++) {
            double a=2*Math.PI*i/64;
            double x=.451*Math.Sign(Math.Cos(a))*Math.Pow(Math.Abs(Math.Cos(a)),.64);
            double y=.269*Math.Sign(Math.Sin(a))*Math.Pow(Math.Abs(Math.Sin(a)),.70)-.01;
            bezel.Add(G.P(x,y,FaceZ(x,y)));
        }
        G.Tube(head,bezel,.009,G.Silver);
        for(int side=-1;side<=1;side+=2) {
            G.Ellipsoid(head,G.P(side*.55,-.01,-.025),new Vector3D(.09,.23,.23),G.Graphite);
            G.Ellipsoid(head,G.P(side*.59,-.01,-.025),new Vector3D(.065,.205,.205),G.Silver);
            G.Ring(head,G.P(side*.641,-.01,-.025),.145,.018,G.Cyan,new Vector3D(1,0,0));
            G.Ellipsoid(head,G.P(side*.648,-.01,-.025),new Vector3D(.015,.118,.118),G.Glass);
            G.Ring(head,G.P(side*.657,-.01,-.025),.075,.008,G.Silver,new Vector3D(1,0,0));
        }
        faceEyes=G.Group(head);
        faceEyes.Transform=eyeScale;
        for(int side=-1;side<=1;side+=2) {
            var arc=new List<Point3D>();
            for(int i=0;i<=16;i++) {
                double x=side*.19-.10+.20*i/16, y=.025+.075*Math.Sin(Math.PI*i/16);
                arc.Add(G.P(x,y,FaceZ(x,y)+.016));
            }
            G.Tube(faceEyes,arc,.019,G.Cyan);
        }
        mouth=G.Group(head);mouth.Transform=mouthScale;var smile=new List<Point3D>();
        for(int i=0;i<=18;i++){double x=-.10+.20*i/18,y=-.105-.045*Math.Sin(Math.PI*i/18);smile.Add(G.P(x,y,FaceZ(x,y)+.018));}
        G.Tube(mouth,smile,.010,G.Cyan);
        G.Tube(head,new[]{G.P(-.24,-.32,.26),G.P(0,-.356,.24),G.P(.24,-.32,.26)},.009,G.CyanDark);
        G.Ellipsoid(head,G.P(0,.384,.10),new Vector3D(.13,.035,.17),G.Graphite);
        G.Ring(head,G.P(0,.417,.10),.085,.012,G.Cyan,new Vector3D(0,1,0));
        // Crown is a modeled gold band and individual conical jewels/spires.
        G.Ring(head,G.P(0,.407,-.01),.335,.025,G.Gold,new Vector3D(0,1,0));
        G.Ring(head,G.P(0,.46,-.01),.338,.018,G.Gold,new Vector3D(0,1,0));
        for(int i=0;i<5;i++) {
            double a=2*Math.PI*i/5+.3, x=Math.Cos(a)*.325,z=Math.Sin(a)*.325-.01;
            double height=.20+(i%2==0?.055:0);
            var spike=G.Group(head);G.Mesh(spike,G.Lathe(new[]{0.0,.04,height},new[]{.085,.060,.001},20),G.Gold);
            spike.Transform=G.At(G.P(x,.44,z),new Vector3D(x*.28,1,z*.28));
            G.Ellipsoid(head,G.P(x+x*.28*height,.44+height,z+z*.28*height),new Vector3D(.028,.028,.028),G.Silver);
            G.Ellipsoid(head,G.P(x+x*.28*height,.448+height,z+z*.28*height+.008),new Vector3D(.017,.017,.017),G.Cyan);
        }
        var jewel=G.Group(head);G.Mesh(jewel,G.Lathe(new[]{-.045,0.0,.045},new[]{0.0,.045,0.0},4),G.Cyan);
        jewel.Transform=new TranslateTransform3D(0,.439,.34);
        Viewport.Camera=new PerspectiveCamera(new Point3D(0,1.91,8),new Vector3D(0,0,-8),new Vector3D(0,1,0),21);
        Viewport.Children.Add(new ModelVisual3D {Content=Scene});
        RenderOptions.SetEdgeMode(Viewport,EdgeMode.Unspecified);
    }
    static double FaceZ(double x,double y){return .40+.055*(1-x*x/(.46*.46))*(1-.35*y*y/(.28*.28));}
    static void BuildVisor(Model3DGroup parent) {
        var mesh=new MeshGeometry3D();int rings=12,steps=80;
        for(int r=0;r<=rings;r++)for(int i=0;i<=steps;i++) {
            double a=2*Math.PI*i/steps,p=(double)r/rings;
            double x=p*.46*Math.Sign(Math.Cos(a))*Math.Pow(Math.Abs(Math.Cos(a)),.59);
            double y=p*.28*Math.Sign(Math.Sin(a))*Math.Pow(Math.Abs(Math.Sin(a)),.63)-.01;
            mesh.Positions.Add(G.P(x,y,FaceZ(x,y)));
            var normal=new Vector3D(.11*x/(.46*.46)*(1-.35*y*y/(.28*.28)),.0385*y/(.28*.28)*(1-x*x/(.46*.46)),1);
            normal.Normalize();mesh.Normals.Add(normal);
        }
        for(int r=0;r<rings;r++)for(int i=0;i<steps;i++) {
            int a=r*(steps+1)+i,b=a+steps+1;
            mesh.TriangleIndices.Add(a);mesh.TriangleIndices.Add(b);mesh.TriangleIndices.Add(b+1);
            mesh.TriangleIndices.Add(a);mesh.TriangleIndices.Add(b+1);mesh.TriangleIndices.Add(a+1);
        }
        mesh.Freeze();G.Mesh(parent,mesh,G.Glass);
    }
    static Transform3D BodyAt(Point3D point,double yaw,double tilt=0) {
        var t=new Transform3DGroup();
        t.Children.Add(new RotateTransform3D(new AxisAngleRotation3D(new Vector3D(0,1,0),yaw)));
        t.Children.Add(new RotateTransform3D(new AxisAngleRotation3D(new Vector3D(0,0,1),tilt)));
        t.Children.Add(new TranslateTransform3D(point.X,point.Y,point.Z));return t;
    }
    public void Update(Motion motion,bool pointLeft=false) {
        presentationRotation.Angle=PresentationYaw;
        double gait=Motion.Clamp(motion.Velocity/1.35), swing=Math.Sin(motion.Phase*2*Math.PI)*.30*gait;
        double dt=Math.Max(0,Math.Min(.1,motion.Time-lastPoseTime));lastPoseTime=motion.Time;
        talkBlend=Motion.Mix(talkBlend,motion.Stage=="speak"?1:0,1-Math.Exp(-dt*12));
        pelvisPose.Set(G.P(motion.Sway,1.48+motion.Bob,0),motion.PelvisYaw,Math.Sin(motion.Phase*2*Math.PI)*gait*.8);
        torsoPose.Set(G.P(motion.Sway,2.13+motion.Bob,0),motion.TorsoYaw,-swing*2);
        neckPose.Set(G.P(motion.Sway*.5,2.63+motion.Bob*.6,0),motion.HeadYaw);
        headPose.Set(G.P(motion.Sway*.5,3.04+motion.Bob*.35,0),motion.HeadYaw,Math.Sin(motion.Time*1.25)*.7+talkBlend*Math.Sin(motion.Time*3)*1.4);
        double blink=motion.Time%4.1>3.88?.5-.5*Math.Cos(Motion.Clamp((motion.Time%4.1-3.88)/.22)*Math.PI*2):0;
        eyeScale.ScaleY=Math.Max(.055,1-blink);
        mouthScale.ScaleY=1+talkBlend*.65*Math.Abs(Math.Sin(motion.Time*12));
        for(int i=0;i<2;i++) {
            var hip=motion.Hip(i);var ankle=motion.Ankle(i);var knee=motion.Knee(i);
            thighs[i].Place(hip,knee,motion.PelvisYaw);shins[i].Place(knee,ankle,motion.PelvisYaw);
            bootPoses[i].Set(G.P(ankle.X,motion.Feet[i].Lift,ankle.Z),motion.Feet[i].Yaw,motion.Feet[i].Roll);
            double side=i==0?-1:1;
            Point3D shoulder=Motion.Rotate(G.P(side*.49,2.34+motion.Bob,0),motion.TorsoYaw);
            shoulder.X+=motion.Sway;
            shoulderPoses[i].Set(shoulder,motion.TorsoYaw);
            double angle=swing*(i==0?-1:1),forward=Math.Sin(angle);
            Point3D elbow=Motion.Rotate(G.P(side*.58,1.94+motion.Bob,forward*.43),motion.TorsoYaw);
            Point3D wrist=Motion.Rotate(G.P(side*.60,1.53+motion.Bob,forward*.80+.06),motion.TorsoYaw);
            double point=((pointLeft?i==0:i==1)?motion.Gesture:0),direction=pointLeft?-1:1;
            elbow=Motion.Mix(elbow,G.P(shoulder.X+direction*.40,shoulder.Y-.12,.15),point);
            wrist=Motion.Mix(wrist,G.P(shoulder.X+direction*.75,shoulder.Y+.21,.24),point);
            upper[i].Place(shoulder,elbow,motion.TorsoYaw);fore[i].Place(elbow,wrist,motion.TorsoYaw);
            var handDirection=wrist-elbow;handDirection.Normalize();
            hands[i].Pose(wrist,handDirection,point,motion.Time,motion.TorsoYaw);
        }
    }
}
}
