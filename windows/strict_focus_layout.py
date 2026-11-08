"""Bounded two-pane geometry; contains no browser or session authority."""
class StudyPaneLayout:
    MIN_PANE=320
    DIVIDER=8
    def __init__(self):
        self.order=['chatgpt','leetcode'];self.active='chatgpt'
        self.side_by_side=True;self.ratio=.5

    def restore_preferences(self,value):
        if not isinstance(value,dict):return
        ratio=value.get('ratio',.5)
        if isinstance(ratio,(int,float)) and not isinstance(ratio,bool) and .0<ratio<1:
            self.ratio=min(.75,max(.25,float(ratio)))
        if value.get('order') in (['chatgpt','leetcode'],['leetcode','chatgpt']):self.order=list(value['order'])
        if value.get('active') in self.order:self.active=value['active']
        if isinstance(value.get('side_by_side'),bool):self.side_by_side=value['side_by_side']

    def select(self,key):
        if key not in self.order:return False
        self.active=key;return True

    def split(self,width):return self.side_by_side and width>=2*self.MIN_PANE+self.DIVIDER

    def resize(self,x,width):
        usable=width-self.DIVIDER
        if not self.split(width):return False
        minimum=max(self.MIN_PANE,round(usable*.25))
        left=min(usable-minimum,max(minimum,int(x)))
        self.ratio=left/usable;return True

    def swap(self):self.order.reverse()

    def drop(self,key,x,y,width,height):
        if key not in self.order or not self.split(width) or not (0<=x<width and 0<=y<height):return False
        divider=self.geometry(width,height)['divider'][0]
        slot=0 if x<divider+self.DIVIDER/2 else 1
        if self.order.index(key)==slot:return False
        self.swap();return True

    def geometry(self,width,height):
        width=max(0,int(width));height=max(0,int(height))
        if not self.split(width):
            return {self.active:(0,0,width,height),'divider':None}
        usable=width-self.DIVIDER
        minimum=max(self.MIN_PANE,round(usable*.25))
        left=min(usable-minimum,max(minimum,round(usable*self.ratio)))
        return {self.order[0]:(0,0,left,height),'divider':(left,0,self.DIVIDER,height),
                self.order[1]:(left+self.DIVIDER,0,usable-left,height)}
