using System;
using System.Collections.Generic;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.Windows.Media.Media3D;

namespace StrictBotNative {
public static class G {
    public static Color C(string value) { return (Color)ColorConverter.ConvertFromString(value); }
    public static SolidColorBrush B(string value) { var b=new SolidColorBrush(C(value)); b.Freeze(); return b; }
    public static Material Mat(string color,double shine=60,string highlight="#ffffff") {
        var m=new MaterialGroup();m.Children.Add(new DiffuseMaterial(B(color)));
        m.Children.Add(new SpecularMaterial(B(highlight),shine));m.Freeze();return m;
    }
    public static Material Glow(string color) {
        var m=new MaterialGroup();m.Children.Add(new DiffuseMaterial(B(color)));
        m.Children.Add(new EmissiveMaterial(B(color)));m.Freeze();return m;
    }
    public static readonly Material Ceramic=Mat("#e2eef5",85,"#ffffff");
    public static readonly Material Silver=Mat("#809bab",100,"#eaffff");
    public static readonly Material Graphite=Mat("#15222e",55,"#7b98b0");
    public static readonly Material Rubber=Mat("#07111b",15,"#283844");
    public static readonly Material Glass=Mat("#020a13",125,"#b9efff");
    public static readonly Material Gold=Mat("#d9a843",100,"#fff0b9");
    public static readonly Material Cyan=Glow("#00dcec");
    public static readonly Material CyanDark=Mat("#006c85",75,"#69ffff");

    static MeshGeometry3D sphere,smallSphere;
    public static MeshGeometry3D Sphere {
        get {
            if(sphere!=null)return sphere;
            sphere=MakeSphere(32,48);return sphere;
        }
    }
    static MeshGeometry3D MakeSphere(int rows,int cols) {
            var mesh=new MeshGeometry3D();
            for(int y=0;y<=rows;y++) {
                double a=Math.PI*y/rows;
                for(int x=0;x<=cols;x++) {
                    double b=2*Math.PI*x/cols;
                    var v=new Vector3D(Math.Sin(a)*Math.Cos(b),Math.Cos(a),Math.Sin(a)*Math.Sin(b));
                    mesh.Positions.Add(new Point3D(v.X,v.Y,v.Z));mesh.Normals.Add(v);
                    mesh.TextureCoordinates.Add(new Point((double)x/cols,(double)y/rows));
                }
            }
            for(int y=0;y<rows;y++)for(int x=0;x<cols;x++) {
                int i=y*(cols+1)+x;
                mesh.TriangleIndices.Add(i);mesh.TriangleIndices.Add(i+1);mesh.TriangleIndices.Add(i+cols+1);
                mesh.TriangleIndices.Add(i+1);mesh.TriangleIndices.Add(i+cols+2);mesh.TriangleIndices.Add(i+cols+1);
            }
            mesh.Freeze();return mesh;
    }
    public static Model3DGroup Group(Model3DGroup parent=null) { var g=new Model3DGroup();if(parent!=null)parent.Children.Add(g);return g; }
    public static GeometryModel3D Ellipsoid(Model3DGroup parent,Point3D center,Vector3D radii,Material material) {
        if(smallSphere==null)smallSphere=MakeSphere(10,16);
        var model=new GeometryModel3D(Math.Max(radii.X,Math.Max(radii.Y,radii.Z))<.095?smallSphere:Sphere,material);
        var transform=new Transform3DGroup();transform.Children.Add(new ScaleTransform3D(radii.X,radii.Y,radii.Z));
        transform.Children.Add(new TranslateTransform3D(center.X,center.Y,center.Z));model.Transform=transform;
        parent.Children.Add(model);return model;
    }
    public static MeshGeometry3D Lathe(double[] ys,double[] rs,int segments=32) {
        var mesh=new MeshGeometry3D();
        for(int j=0;j<ys.Length;j++)for(int i=0;i<=segments;i++) {
            double a=i*2*Math.PI/segments;
            double slope=j==0?(rs[1]-rs[0])/(ys[1]-ys[0]):j==ys.Length-1?
                (rs[j]-rs[j-1])/(ys[j]-ys[j-1]):(rs[j+1]-rs[j-1])/(ys[j+1]-ys[j-1]);
            var normal=new Vector3D(Math.Cos(a),-slope,Math.Sin(a));normal.Normalize();
            mesh.Positions.Add(new Point3D(rs[j]*Math.Cos(a),ys[j],rs[j]*Math.Sin(a)));
            mesh.Normals.Add(normal);mesh.TextureCoordinates.Add(new Point((double)i/segments,(double)j/(ys.Length-1)));
        }
        for(int j=0;j<ys.Length-1;j++)for(int i=0;i<segments;i++) {
            int a=j*(segments+1)+i,b=a+segments+1;
            mesh.TriangleIndices.Add(a);mesh.TriangleIndices.Add(b);mesh.TriangleIndices.Add(a+1);
            mesh.TriangleIndices.Add(a+1);mesh.TriangleIndices.Add(b);mesh.TriangleIndices.Add(b+1);
        }
        mesh.Freeze();return mesh;
    }
    public static GeometryModel3D Mesh(Model3DGroup parent,MeshGeometry3D geometry,Material material) {
        var m=new GeometryModel3D(geometry,material);parent.Children.Add(m);return m;
    }
    public static GeometryModel3D Shell(Model3DGroup parent,double length,double radius,Material material,double flatten=.80) {
        var mesh=Lathe(new[]{0.0,.035,length*.16,length*.64,length*.92,length},
                      new[]{radius*.45,radius*.76,radius,radius*.90,radius*.63,radius*.43});
        var model=Mesh(parent,mesh,material);model.Transform=new ScaleTransform3D(1,1,flatten);return model;
    }
    public static Model3DGroup Ring(Model3DGroup parent,Point3D center,double radius,double tube,Material material,Vector3D axis) {
        var group=Group(parent);var mesh=new MeshGeometry3D();int n=40,m=10;
        for(int i=0;i<=n;i++)for(int j=0;j<=m;j++) {
            double a=2*Math.PI*i/n,b=2*Math.PI*j/m;
            mesh.Positions.Add(new Point3D((radius+tube*Math.Cos(b))*Math.Cos(a),tube*Math.Sin(b),(radius+tube*Math.Cos(b))*Math.Sin(a)));
            mesh.Normals.Add(new Vector3D(Math.Cos(a)*Math.Cos(b),Math.Sin(b),Math.Sin(a)*Math.Cos(b)));
        }
        for(int i=0;i<n;i++)for(int j=0;j<m;j++){int a=i*(m+1)+j,b=a+m+1;
            mesh.TriangleIndices.Add(a);mesh.TriangleIndices.Add(a+1);mesh.TriangleIndices.Add(b);
            mesh.TriangleIndices.Add(a+1);mesh.TriangleIndices.Add(b+1);mesh.TriangleIndices.Add(b);}
        mesh.Freeze();Mesh(group,mesh,material);
        group.Transform=At(center,axis);return group;
    }
    public static Transform3D At(Point3D point,Vector3D direction) {
        return Frame(point,direction,new Vector3D(0,0,1));
    }
    public static Transform3D Frame(Point3D point,Vector3D direction,Vector3D front) {
        return new MatrixTransform3D(FrameMatrix(point,direction,front));
    }
    public static Matrix3D FrameMatrix(Point3D point,Vector3D direction,Vector3D front) {
        Vector3D y=direction;y.Normalize();
        Vector3D z=front-y*Vector3D.DotProduct(front,y);
        if(z.Length<.001)z=new Vector3D(1,0,0)-y*y.X;
        z.Normalize();Vector3D x=Vector3D.CrossProduct(y,z);x.Normalize();z=Vector3D.CrossProduct(x,y);
        return new Matrix3D(x.X,x.Y,x.Z,0,y.X,y.Y,y.Z,0,z.X,z.Y,z.Z,0,point.X,point.Y,point.Z,1);
    }
    public static GeometryModel3D Tube(Model3DGroup parent,IList<Point3D> points,double radius,Material material,int sides=9) {
        var mesh=new MeshGeometry3D();
        for(int i=0;i<points.Count;i++) {
            Vector3D dir=(i==points.Count-1?points[i]-points[i-1]:points[i+1]-points[i]);dir.Normalize();
            Vector3D a=Vector3D.CrossProduct(dir,Math.Abs(dir.Y)<.9?new Vector3D(0,1,0):new Vector3D(1,0,0));a.Normalize();
            Vector3D b=Vector3D.CrossProduct(dir,a);
            for(int j=0;j<=sides;j++) {
                double angle=2*Math.PI*j/sides;Vector3D normal=a*Math.Cos(angle)+b*Math.Sin(angle);
                mesh.Positions.Add(points[i]+normal*radius);mesh.Normals.Add(normal);
            }
        }
        for(int i=0;i<points.Count-1;i++)for(int j=0;j<sides;j++){int a=i*(sides+1)+j,b=a+sides+1;
            mesh.TriangleIndices.Add(a);mesh.TriangleIndices.Add(a+1);mesh.TriangleIndices.Add(b);
            mesh.TriangleIndices.Add(a+1);mesh.TriangleIndices.Add(b+1);mesh.TriangleIndices.Add(b);}
        mesh.Freeze();return Mesh(parent,mesh,material);
    }
    public static Material TextMaterial(string text,Color color) {
        var label=new TextBlock {Text=text,Foreground=new SolidColorBrush(color),FontFamily=new FontFamily("Segoe UI"),
            FontWeight=FontWeights.Bold,FontSize=76,TextAlignment=TextAlignment.Center,Width=256,Height=128};
        label.Measure(new Size(256,128));label.Arrange(new Rect(0,0,256,128));label.UpdateLayout();
        var bitmap=new RenderTargetBitmap(256,128,96,96,PixelFormats.Pbgra32);bitmap.Render(label);bitmap.Freeze();
        var brush=new ImageBrush(bitmap);brush.Freeze();var mat=new EmissiveMaterial(brush);mat.Freeze();return mat;
    }
    public static void Quad(Model3DGroup group,double x,double y,double z,double width,double height,Material material) {
        var mesh=new MeshGeometry3D();
        mesh.Positions=new Point3DCollection(new[]{new Point3D(x-width/2,y-height/2,z),new Point3D(x+width/2,y-height/2,z),
            new Point3D(x+width/2,y+height/2,z),new Point3D(x-width/2,y+height/2,z)});
        mesh.TextureCoordinates=new PointCollection(new[]{new Point(0,1),new Point(1,1),new Point(1,0),new Point(0,0)});
        mesh.TriangleIndices=new Int32Collection(new[]{0,1,2,0,2,3});mesh.Freeze();Mesh(group,mesh,material);
    }
    public static Point3D P(double x,double y,double z){return new Point3D(x,y,z);}
}
}
