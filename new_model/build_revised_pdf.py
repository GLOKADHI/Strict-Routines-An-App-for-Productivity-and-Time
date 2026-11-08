from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    Image,
    KeepTogether,
    PageTemplate,
    PageBreak,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)


ROOT = Path(__file__).resolve().parent
IMAGE_DIR = ROOT / "tmp" / "pdfs" / "extracted-images"
OUTPUT_DIR = ROOT / "output" / "pdf"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_PDF = OUTPUT_DIR / "Solar_Powered_Hybrid_Drone_IEEE_Paper_revised.pdf"


PAGE_W, PAGE_H = letter
LEFT = 54
RIGHT = 54
GUTTER = 18
COL_W = (PAGE_W - LEFT - RIGHT - GUTTER) / 2
BOTTOM = 48
TOP = 50


styles = getSampleStyleSheet()
body = ParagraphStyle(
    "Body",
    parent=styles["Normal"],
    fontName="Times-Roman",
    fontSize=8.55,
    leading=10.15,
    alignment=TA_JUSTIFY,
    spaceAfter=5.2,
    splitLongWords=True,
)
body_tight = ParagraphStyle(
    "BodyTight",
    parent=body,
    spaceAfter=3.4,
)
abstract = ParagraphStyle(
    "Abstract",
    parent=body,
    fontSize=8.3,
    leading=9.55,
    spaceAfter=3.0,
)
keywords = ParagraphStyle(
    "Keywords",
    parent=body,
    fontSize=8.3,
    leading=9.55,
    spaceAfter=1.5,
)
section = ParagraphStyle(
    "Section",
    parent=body,
    fontName="Times-Bold",
    fontSize=9.6,
    leading=11.2,
    alignment=TA_CENTER,
    spaceBefore=2,
    spaceAfter=4.5,
    keepWithNext=True,
)
subsection = ParagraphStyle(
    "Subsection",
    parent=body,
    fontName="Times-Bold",
    fontSize=8.7,
    leading=10.2,
    alignment=TA_LEFT,
    spaceBefore=2,
    spaceAfter=2.7,
    keepWithNext=True,
)
subsub = ParagraphStyle(
    "Subsub",
    parent=body,
    fontName="Times-Bold",
    fontSize=8.45,
    leading=9.8,
    spaceBefore=1.5,
    spaceAfter=2,
    keepWithNext=True,
)
caption = ParagraphStyle(
    "Caption",
    parent=body,
    fontName="Times-Roman",
    fontSize=7.3,
    leading=8.2,
    alignment=TA_CENTER,
    spaceBefore=2,
    spaceAfter=5,
)
table_text = ParagraphStyle(
    "TableText",
    parent=body,
    fontName="Times-Roman",
    fontSize=7.15,
    leading=8.0,
    alignment=TA_CENTER,
    spaceAfter=0,
)
table_head = ParagraphStyle(
    "TableHead",
    parent=table_text,
    fontName="Times-Bold",
)
reference = ParagraphStyle(
    "Reference",
    parent=body,
    fontSize=7.15,
    leading=8.05,
    leftIndent=0,
    firstLineIndent=0,
    spaceAfter=1.8,
)
title_style = ParagraphStyle(
    "Title",
    parent=body,
    fontName="Times-Bold",
    fontSize=15.2,
    leading=18,
    alignment=TA_CENTER,
    spaceAfter=9,
)
author_style = ParagraphStyle(
    "Author",
    parent=body,
    fontName="Times-Roman",
    fontSize=8.7,
    leading=10.4,
    alignment=TA_CENTER,
    spaceAfter=0,
)


def P(text, style=body):
    return Paragraph(text, style)


def figure(path_name, caption_text, width=COL_W, max_height=None):
    path = IMAGE_DIR / path_name
    image = Image(str(path))
    scale = width / image.imageWidth
    height = image.imageHeight * scale
    if max_height and height > max_height:
        scale = max_height / image.imageHeight
        width = image.imageWidth * scale
        height = max_height
    image.drawWidth = width
    image.drawHeight = height
    return KeepTogether([image, P(caption_text, caption)])


def make_table(data, widths, caption_text, font_size=7.15):
    converted = []
    for r, row in enumerate(data):
        converted.append([
            Paragraph(str(cell), table_head if r == 0 else table_text)
            for cell in row
        ])
    tbl = Table(converted, colWidths=widths, repeatRows=1, hAlign="CENTER")
    tbl.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.35, colors.black),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E8EEF7")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 2.4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.4),
    ]))
    return KeepTogether([tbl, P(caption_text, caption)])


class IEEEReport(BaseDocTemplate):
    def __init__(self, filename, **kwargs):
        super().__init__(filename, pagesize=letter, leftMargin=LEFT, rightMargin=RIGHT,
                         topMargin=TOP, bottomMargin=BOTTOM, **kwargs)
        top_frame = Frame(LEFT, 422, PAGE_W - LEFT - RIGHT, PAGE_H - 422 - TOP,
                          id="top", leftPadding=0, rightPadding=0, topPadding=0,
                          bottomPadding=0)
        left_frame = Frame(LEFT, BOTTOM, COL_W, 360, id="left", leftPadding=0,
                           rightPadding=0, topPadding=0, bottomPadding=0)
        right_frame = Frame(LEFT + COL_W + GUTTER, BOTTOM, COL_W, 360, id="right",
                            leftPadding=0, rightPadding=0, topPadding=0,
                            bottomPadding=0)
        later_height = PAGE_H - TOP - BOTTOM
        later_left_frame = Frame(LEFT, BOTTOM, COL_W, later_height, id="later-left",
                                 leftPadding=0, rightPadding=0, topPadding=0,
                                 bottomPadding=0)
        later_right_frame = Frame(LEFT + COL_W + GUTTER, BOTTOM, COL_W,
                                  later_height, id="later-right", leftPadding=0,
                                  rightPadding=0, topPadding=0, bottomPadding=0)
        self.addPageTemplates([
            PageTemplate(id="First", frames=[top_frame, left_frame, right_frame],
                         onPage=self._on_page),
            PageTemplate(id="Later", frames=[later_left_frame, later_right_frame],
                         onPage=self._on_page),
        ])

    def _on_page(self, canvas, doc):
        if doc.page > 1:
            canvas.setFont("Times-Roman", 7)
            canvas.drawCentredString(PAGE_W / 2, 28, str(doc.page))

    def afterPage(self):
        if self.page == 1:
            self.handle_nextPageTemplate("Later")


def author_block(name, line2, affiliation, city, email):
    return P(
        f"{name}<br/>{line2}<br/><i>Department of Computer Science and Engineering</i><br/>"
        f"<i>{affiliation}</i><br/><i>{city}</i><br/>{email}",
        author_style,
    )


def story():
    s = []
    s.append(P("Solar-Powered Hybrid Drone for Extended Flight Duration", title_style))
    authors = Table([
        [
            author_block("Lok Adhithya G", "IV Year, B.E.-CSE-AI &amp; Robotics", "Sathyabama Institute of Science and Technology", "Chennai, Tamil Nadu", "lok.adhithya@gmail.com"),
            author_block("Arokya Suman Raj", "IV Year, B.E.-CSE-AI &amp; Robotics", "Sathyabama Institute of Science and Technology", "Chennai, Tamil Nadu", "Suman.314.shs@gmail.com"),
            author_block("Dr. A. Muthulakshmi", "Assistant Professor", "Sathyabama Institute of Science and Technology", "Chennai, Tamil Nadu", "muthulakshmi.a.cse@sathyabama.ac.in"),
        ]
    ], colWidths=[168, 168, 168], hAlign="CENTER")
    authors.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 2),
        ("RIGHTPADDING", (0, 0), (-1, -1), 2),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    s.append(authors)
    s.append(P(
        "<b>Abstract-</b> Multirotor UAV missions are constrained by two linked requirements: the vehicle must complete the assigned route, and it must do so before the available energy is exhausted. A conventional platform powered only by a lithium-polymer battery commonly remains airborne for approximately twenty-five to thirty minutes [CITATION CHECK REQUIRED], limiting coverage and increasing the need for battery changes during extended operations. The limitation is especially important in agriculture, infrastructure inspection, and disaster-response missions, where repeated landings can interrupt the survey. This paper presents a solar-hybrid flight-endurance estimation and adaptive power-allocation system. Given irradiance, payload, altitude, and telemetry variables, the system predicts whether the vehicle is operating in a solar-sufficient or battery-assisted mode and estimates achievable flight endurance in minutes. The resulting predictions are intended to support preflight feasibility checks and in-flight decisions about whether a planned route can be completed. The framework also estimates the share of propulsion demand supplied by the photovoltaic subsystem, allowing operators to plan longer missions with a clearer understanding of the available energy margin.",
        abstract,
    ))
    s.append(P(
        "<b>Keywords-</b> Solar Energy, Photovoltaic (PV) Cells, Flight Endurance Prediction, Hybrid Propulsion, Maximum Power Point Tracking (MPPT), Battery State of Charge, Supervised Machine Learning, Artificial Neural Network.",
        keywords,
    ))

    s.append(P("I. INTRODUCTION", section))
    s.append(P(
        "UAVs have become operational tools in agriculture, infrastructure inspection, disaster response, and environmental monitoring [CITATION CHECK REQUIRED]. For most commercial multirotor vehicles, the energy available at takeoff is dominated by a single lithium-polymer battery pack. Consequently, the time available for a sortie is largely fixed once the vehicle leaves the ground. Completing a field survey, pipeline inspection, or perimeter sweep therefore depends on whether that finite interval covers the planned route without an interruption.",
    ))
    s.append(P(
        "The endurance of many commercial multirotors remains well below thirty minutes [2]. Even a moderate extension of per-sortie duration can reduce the number of battery changes, landings, and repositioning flights needed for a large-area task. The practical consequence is direct: the maximum area that one operator can inspect during a working day is coupled to the energy margin of each flight.",
    ))
    s.append(P(
        "Mission planning is further complicated by changing weather and solar availability. Operators may not know whether a particular battery-and-panel combination will support the complete route, which can result in unnecessarily conservative plans or an aborted flight after the battery reaches a critical level. A useful decision aid should therefore estimate both the remaining flight time and whether the instantaneous balance between harvested and consumed power is sustainable.",
    ))
    s.append(P(
        "The proposed system addresses this requirement with two related predictions. First, it classifies the current flight segment as solar-sufficient or battery-assisted, where solar-sufficient denotes operation without net battery drain. Second, it performs a regression estimate of the total achievable endurance, expressed in minutes, for the current mission profile. Together, these outputs answer the operational question of whether a route is feasible more concretely than a qualitative statement about solar conditions.",
    ))
    s.append(P(
        "Four supervised classifiers - Logistic Regression, Support Vector Machine (SVM), Random Forest (RF), and Artificial Neural Network (ANN) - are compared for the power-mode task. Linear Regression is used for endurance estimation. The classifier results are evaluated with standard measures, while the regression estimate is checked against simulated flight-endurance data. The remainder of the paper is organized as follows: Section II reviews prior solar-UAV and adaptive-power studies; Section III presents data preparation, feature selection, models, implementation, and metrics; Section IV reports the illustrative experiments; and Section V concludes the paper and identifies future work.",
    ))

    s.append(P("II. RELATED WORKS", section))
    related = [
        "[1] Lightweight photovoltaic cells are integrated directly with a quadcopter frame so that the battery can receive charge during flight. The study emphasizes panel placement and the associated power electronics, while also noting that added solar area increases both mass and aerodynamic drag. The energy benefit must therefore be evaluated together with the penalties introduced by the retrofit.",
        "[2] A systematic review surveys solar-powered UAV applications including surveillance, precision agriculture, disaster response, communication relay, weather observation, and wildfire monitoring. It reports that in-flight harvesting can extend mission time and reduce dependence on conventional recharging. The review also describes very large fixed-wing systems reported above 70,000 feet for civilian and defense applications.",
        "[3] SUAV:Q is presented as a transformable hybrid solar UAV that combines efficient fixed-wing cruise with the hovering capability of a multirotor. The design illustrates the persistent trade-off between endurance and maneuverability and shows how mechanical reconfiguration can be used to balance those requirements.",
        "[4] An existing two-meter-wingspan radio-controlled aircraft is converted into a solar-assisted platform. The work selects a compatible photovoltaic subsystem and power architecture for the retrofit, and flight tests report an endurance improvement associated with the added solar input.",
        "[5] A four-meter-wingspan solar aircraft is developed for low-altitude sensing. The larger wing provides room for more photovoltaic cells, enabling the aircraft to carry a heavier sensing payload and remain airborne for more than twenty-four continuous hours.",
        "[6] A solar UAV for environmental monitoring is designed around a six-hour continuous-flight objective. Monocrystalline cells, wing aspect ratio, and motor count are iterated together, producing an approximately 5.5 kg configuration predicted to fly from mid-morning through mid-afternoon under representative regional solar conditions.",
        "[7] AtlantikSolar demonstrates the feasibility of sustained solar flight with a small hand-launched aircraft that completed a twenty-eight-hour continuous flight. The resulting measurements also provide a reference for checking solar-UAV performance models.",
        "[8] A photovoltaic retrofit is evaluated for a conventional electric UAV through an energy-balance model. The predicted endurance increase closely follows the later flight measurement, supporting the use of energy accounting during the design stage before hardware is finalized.",
        "[9] Brushless DC motors, monocrystalline cells, a Li-Po battery, an Arduino Uno, and an RF transmitter are combined in a radio-controlled solar quadcopter. The flight trials report longer operation when the panels contribute power alongside the battery.",
        "[10] This review examines machine-learning applications in solar-UAV design, including energy-harvesting optimization, flight-mode selection, and predictive maintenance. Its discussion of adaptive power allocation motivates the learned classifier used in the present framework.",
        "[11] A mini solar-assisted quadcopter is instrumented with an eight-cell array and a Li-Po battery. Measurements collected during peak sunlight indicate a lower effective battery discharge rate and an additional ten to fifteen minutes of flight time, although the contribution decreases at higher altitude.",
        "[12] Solar assistance is also examined at the micro-air-vehicle scale. A 71 g rotary-wing platform achieves an average 3.5-minute flight, requires approximately 68 minutes to recharge under standard irradiance, and can remain dormant without sunlight for up to 38 days.",
        "[13] A PID-controlled solar quadcopter is modeled in MATLAB/Simulink, including vehicle dynamics, panel output, and battery behavior. The study shows how flight duration can be estimated in simulation before physical flight testing.",
        "[14] State-of-charge trajectories are planned for a low-altitude solar-powered convertible UAV across climb, cruise, and loiter modes. The controller can thus anticipate the energy demand of an upcoming maneuver instead of reacting only after the demand occurs.",
    ]
    for item in related:
        s.append(P(item, body_tight))
    s.append(P(
        "Collectively, these studies show that solar-hybrid propulsion can extend UAV endurance across substantially different airframe scales. They also indicate that the achievable benefit depends on vehicle size, mission timing, and control strategy. A comparatively less explored problem is a like-for-like comparison of supervised classifiers for solar-sufficiency mode together with a numerical endurance model on one multirotor platform. The present paper addresses that combination.",
    ))

    s.append(P("III. METHODOLOGY", section))
    s.append(P(
        "Uncertainty in available flight time is a central operational limitation for multirotor UAVs. A mission plan is credible only when the combined battery and photovoltaic energy can support the planned activity. The proposed methodology therefore targets a prediction that can be consulted before launch or during flight, rather than an explanation discovered only after the battery becomes depleted.",
    ))
    s.append(P(
        "The system predicts the power-sufficiency mode of a flight segment and its estimated endurance from environmental and telemetry variables. Logistic Regression, SVM, RF, and ANN are trained for classification, while Linear Regression is used for endurance estimation. Model performance is evaluated independently after preprocessing. The data source combines instrumented flight logs with MATLAB/Simulink-based flight simulations [13], [14].",
    ))
    s.append(figure("p3-img1.png", "Fig. 1. System architecture of the solar-powered hybrid drone.", max_height=151))
    s.append(P(
        "The intended implementation is an end-to-end ground-control dashboard. It accepts current mission and telemetry values, determines whether the vehicle can sustain flight from solar input, and reports an estimate of the remaining endurance. This design turns the analysis into an AI-assisted mission-planning aid that can be used before or during a sortie.",
    ))

    s.append(P("A. Data Collection", subsection))
    s.append(P(
        "The telemetry dataset is assembled from instrumented flight trials and MATLAB/Simulink simulations. At fixed logging intervals it records solar irradiance, altitude, airspeed, payload mass, PV-array output, and battery voltage, together with the power-sufficiency label and remaining endurance for the corresponding segment. Figure 2 shows a representative sample; the attributes used by the framework are irradiance, altitude, airspeed, payload, PV output, battery voltage, mode, and endurance.",
    ))
    s.append(figure("p3-img2.png", "Fig. 2. Sample telemetry dataset (illustrative).", max_height=88))

    s.append(P("B. Data Pre-processing", subsection))
    s.append(P(
        "Logged telemetry can contain missing samples, GPS errors, and short irradiance spikes caused by panel glare. These imperfections are addressed before training through imputation and the removal of irrelevant or redundant attributes. Continuous inputs are standardized to zero mean and unit standard deviation. This step is important because the gradient-based Logistic and Linear Regression fits, as well as the SVM and ANN models, can be affected by differences in feature scale.",
    ))

    s.append(P("C. Feature and Target Selection", subsection))
    s.append(P(
        "For power-mode classification, irradiance, altitude, airspeed, payload, and PV output form the input vector, while the target label is either solar-sufficient or battery-assisted. The endurance regressor uses the same variables together with the current battery voltage and predicts endurance in minutes. The two tasks therefore share most environmental and vehicle descriptors while retaining different targets.",
    ))

    s.append(P("D. Machine Learning Models", subsection))
    s.append(P(
        "Classification assigns one of the two power-sufficiency labels to a new telemetry observation using patterns learned from labeled examples. The four classifiers evaluated here are Logistic Regression, SVM, RF, and ANN. Regression instead maps the predictor variables to a continuous target, for which Linear Regression is used. A Kalman-filter state estimator is also applied to track battery state of charge and provide a smoothed input to the predictive models. The preprocessed data is partitioned into training and testing subsets using an 80:20 ratio; only the training subset is used for fitting.",
    ))
    s.append(P("<b>a) Logistic Regression:</b> Logistic Regression is used as a binary classifier for the solar-sufficient and battery-assisted states. It maps the linear score to a probability through a sigmoid function, with the decision boundary determined from that probability model. The function used is", body_tight))
    s.append(P("f(x) = 1 / (1 + e^-x)", body_tight))
    s.append(P("<b>b) Support Vector Machine:</b> The SVM classifier searches for a separating hyperplane in the feature space. The boundary is selected using support vectors so that the two telemetry classes are separated with the desired margin. Although SVM can also be used for regression, only its classification form is used here.", body_tight))
    s.append(P("<b>c) Random Forest:</b> RF combines multiple decision trees, each trained from a different sample of the data. A majority vote across the trees determines the predicted class. This ensemble structure is intended to be more robust than relying on a single tree.", body_tight))
    s.append(P("<b>d) Artificial Neural Network:</b> The ANN is a feed-forward model with an input layer matched to the feature vector, one or more nonlinear hidden layers, and a sigmoid output for the binary class. Backpropagation updates the weights during training, enabling the model to represent interactions among irradiance, altitude, payload, and airspeed that may not be captured by a simple linear boundary.", body_tight))
    s.append(P("<b>e) Linear Regression:</b> Linear Regression is used for the continuous endurance target. It represents the relationship between the predictor vector and the estimated endurance as", body_tight))
    s.append(P("y = A*x + B", body_tight))
    s.append(P("Here, y is the estimated flight endurance, x is the vector of predictors, and A and B are the fitted coefficients.", body_tight))
    s.append(P("<b>f) Kalman-Filter State-of-Charge Estimator:</b> The recursive estimator combines voltage, current, and temperature measurements to produce a smoothed, drift-corrected battery state-of-charge estimate under the variable current demand of multirotor flight. That estimate is supplied to the classifiers and regressor as a cleaner state signal.", body_tight))

    s.append(P("E. Implementation of Models", subsection))
    s.append(P(
        "The models are configured with task-specific parameters rather than relying entirely on defaults. Logistic Regression uses the 'lbfgs' solver and a maximum of 1000 iterations. The SVM uses a linear kernel with probability estimates enabled, and the RF contains 500 estimators. The ANN has two hidden layers containing 32 and 16 neurons, ReLU hidden activations, a sigmoid output, the Adam optimizer, and 200 training epochs. The Kalman filter process- and measurement-noise covariance matrices are tuned using a held-out calibration segment. Each configured model is fitted on the training partition and evaluated on the testing partition described in Section III-C.",
    ))

    s.append(P("F. Performance Evaluation Metrics", subsection))
    s.append(P(
        "Classifier evaluation uses four outcome counts: TP denotes a solar-sufficient segment correctly identified as solar-sufficient, TN denotes a battery-assisted segment correctly identified as battery-assisted, FP denotes a battery-assisted segment incorrectly labeled solar-sufficient, and FN denotes a solar-sufficient segment incorrectly labeled battery-assisted.",
    ))
    s.append(P("Accuracy = (TP + TN) / (TP + FP + FN + TN)", body_tight))
    s.append(P("Precision = TP / (TP + FP)", body_tight))
    s.append(P("Recall = TP / (TP + FN)", body_tight))
    s.append(P("F1-score = 2 * (Recall * Precision) / (Recall + Precision)", body_tight))
    s.append(P(
        "After these measures are computed for each classifier, the model with the most suitable performance can be selected for use in the dashboard.",
    ))

    s.append(P("G. Energy-Balance Interpretation", subsection))
    s.append(P(
        "The classification target is linked to the instantaneous energy balance of the aircraft. A useful conceptual form is P_net = P_PV - P_load, where P_PV represents the usable photovoltaic contribution after power-conditioning losses and P_load represents the electrical demand of propulsion, control, sensing, and onboard computation. When P_net is nonnegative, the flight segment can be treated as solar-sufficient because the battery does not need to supply the net deficit. When P_net is negative, the segment is battery-assisted. The expression describes the meaning of the label; the trained classifier still learns the label from the telemetry variables listed in Section III-C.",
    ))
    s.append(P(
        "In an implementation, the two terms should be derived from measurements taken at comparable points in the power architecture. Panel output measured before the MPPT controller should not be compared directly with load power measured after the DC-DC stage without accounting for conversion efficiency. The system architecture in Fig. 1 therefore places the MPPT controller, battery-management system, and power-distribution bus between the energy sources and the propulsion subsystem. This distinction is important because a classification error near the mode boundary may arise from measurement mismatch rather than from the learning algorithm alone.",
    ))
    s.append(P(
        "The selected features provide complementary information about this balance. PV output and irradiance describe available solar input, while altitude, airspeed, and payload influence the power required to maintain the mission state. Battery voltage and the Kalman-filter state-of-charge estimate describe the remaining energy reserve. No single feature is expected to determine the mode in every flight condition; the purpose of the supervised models is to combine these measurements into an operational prediction.",
    ))

    s.append(P("H. Operational Workflow and Deployment Considerations", subsection))
    s.append(P(
        "The proposed dashboard can be viewed as five connected stages. First, it acquires a current telemetry record or a preflight mission estimate. Second, it checks that the record contains the required variables and that the values fall within the valid sensor ranges. Third, it applies the same imputation and standardization procedures used during model training. Fourth, it passes the prepared vector to the mode classifier and endurance regressor. Finally, it presents the predicted mode, the endurance estimate, and a short status message to the operator. Keeping the sequence identical between training and deployment reduces the risk that a model receives inputs in an unfamiliar scale or order.",
    ))
    s.append(P(
        "The dashboard should distinguish a prediction from a control command. Its output is intended to support mission planning and operator judgment; it does not replace the flight controller, battery-management system, or emergency failsafe. A practical interface should also display the age of the latest telemetry sample and identify missing or substituted values. If the input stream becomes stale, the system should report that the estimate is unavailable or degraded instead of presenting an apparently precise value based on old data.",
    ))
    s.append(P(
        "Near the solar-sufficiency boundary, small changes in irradiance, attitude, or motor demand can cause the predicted mode to change between consecutive samples. A deployment implementation can reduce this visual oscillation by applying a short temporal filter or a hysteresis rule to the displayed status. Such smoothing should be applied only to the operator-facing message unless it is separately validated, because excessive smoothing could delay a genuine transition to battery-assisted operation. The present paper does not report an evaluation of this interface-level decision rule; it is a design consideration for future implementation.",
    ))

    s.append(P("IV. EXPERIMENTAL RESULTS", section))
    s.append(P(
        "The experiments assess the classifiers described in Section III for solar-sufficiency prediction and evaluate the endurance regressor against simulated flight data. All values reported in this section are illustrative and demonstrate the intended reporting format; the authors should replace them with measurements from their own flight tests or simulations before submission.",
    ))
    s.append(make_table([
        ["Method", "Accuracy\n(%)", "Precision", "Recall", "F1-score"],
        ["LR", "83.10", "84.00", "82.50", "83.20"],
        ["SVM", "85.40", "86.20", "84.80", "85.50"],
        ["RF", "81.70", "82.90", "83.60", "83.20"],
        ["ANN", "88.60", "89.10", "90.00", "89.50"],
    ], [COL_W * 0.20, COL_W * 0.16, COL_W * 0.22, COL_W * 0.20, COL_W * 0.22], "TABLE I. PERFORMANCE OF THE CLASSIFIERS (ILLUSTRATIVE)"))
    s.append(P(
        "Table I reports the proportion of flight segments assigned to their correct power-sufficiency mode. In these illustrative results, ANN has the highest accuracy, followed by SVM, LR, and RF. Table II adds model build time and instance-level counts.",
    ))
    s.append(make_table([
        ["Classifier", "Build Time\n(s)", "Correct", "Incorrect", "Accuracy\n(%)"],
        ["LR", "0.05", "92", "19", "82.88"],
        ["SVM", "0.09", "95", "16", "85.59"],
        ["RF", "0.42", "90", "21", "81.08"],
        ["ANN", "3.65", "98", "13", "88.29"],
    ], [COL_W * 0.25, COL_W * 0.20, COL_W * 0.18, COL_W * 0.19, COL_W * 0.18], "TABLE II. COMPARISON OF CLASSIFIER PERFORMANCE METRICS (ILLUSTRATIVE)"))
    s.append(figure("p5-img1.png", "Fig. 3. Comparison of evaluation metrics for different classifiers (illustrative).", max_height=133))
    s.append(P(
        "The ordering in Table I, Table II, and Fig. 3 places ANN first, followed by SVM, LR, and RF. This outcome is consistent with the expectation that ANN can model nonlinear relationships among irradiance, altitude, payload, and airspeed. The additional build time shown in Table II is the corresponding computational trade-off. The small difference between the ANN accuracy values in Tables I and II should be checked against the authors' original evaluation procedure before publication.",
    ))
    s.append(P(
        "Endurance estimation is evaluated separately with three simulated flight sets containing 50 segments each. For every segment, the simulated endurance is compared with the interval reported by the regression model. The results are summarized in Table III.",
    ))
    s.append(make_table([
        ["Set", "Total", "Within\nRange", "Outside\nRange", "Accuracy\n(%)"],
        ["Set-1 (50)", "50", "41", "9", "82"],
        ["Set-2 (50)", "50", "37", "13", "74"],
        ["Set-3 (50)", "50", "36", "14", "72"],
    ], [COL_W * 0.25, COL_W * 0.16, COL_W * 0.20, COL_W * 0.20, COL_W * 0.19], "TABLE III. COMPARISON OF FLIGHT-ENDURANCE PREDICTION RESULTS WITH SIMULATED DATA (ILLUSTRATIVE)"))
    s.append(P(
        "Across the three sets, an average of 38 segments per set falls within the reported endurance range, with 41 as the highest count and 36 as the lowest. The corresponding mean agreement is approximately 76%. These illustrative values indicate that the Linear Regression estimator, supported by the Kalman-filter state-of-charge input, may be useful for mission planning, although its agreement is lower than the classifier accuracy reported in Table I. The definition of a prediction 'range' for the Linear Regression output should be documented and reviewed by the authors.",
    ))
    s.append(P("A. Interpretation of Classification Results", subsection))
    s.append(P(
        "The classifier comparison suggests a useful distinction between predictive quality and computational cost. In the illustrative tables, ANN obtains the strongest classification metrics, which is consistent with a model that can represent interactions among environmental and vehicle variables. Its build time is also the largest of the four methods. SVM provides a closer performance-to-cost alternative, while LR offers a simpler decision function that may be easier to inspect and deploy on limited hardware. RF provides a nonlinear baseline, but its illustrative accuracy is lower than the other methods in this data presentation.",
    ))
    s.append(P(
        "These observations should not be interpreted as a universal ranking of the algorithms. The numbers depend on the data distribution, the class balance, the choice of training and testing records, and the parameter settings described in Section III-E. A stronger experimental study should report the number of flight logs, the number of segments per log, the class counts, and whether the split was performed by flight rather than by individual rows. Splitting adjacent samples from the same flight across both partitions can make the test result appear stronger because the two partitions may contain highly correlated operating states.",
    ))
    s.append(P("B. Interpretation of Endurance Estimation", subsection))
    s.append(P(
        "The endurance results require a more specific interpretation than the classifier percentages. Table III counts a segment as successful when its simulated endurance lies within the reported prediction range. This is a coverage-style measure, not necessarily the same as a point-estimation accuracy. To make the regression result reproducible, the authors should state how the interval is constructed, whether it is symmetric around the Linear Regression output, and whether its width changes with battery state, payload, or solar conditions.",
    ))
    s.append(P(
        "When measured data are available, the regression analysis should be complemented by error statistics such as mean absolute error, root mean square error, and the coefficient of determination, together with a plot of predicted versus observed endurance. These quantities are not added to the present paper because the source document does not provide the underlying predictions. Reporting them in a later experimental version would make it easier to compare the endurance model with alternative regressors without changing the classification task.",
    ))
    s.append(P("C. Validity Boundaries and Practical Limitations", subsection))
    s.append(P(
        "Several limitations define the current scope of the framework. First, the reported values are illustrative, so they do not establish performance on a physical aircraft. Second, solar irradiance, payload, and airspeed may vary outside the conditions represented in the training data. A model exposed to a new season, a different panel orientation, or a different battery aging state may therefore require recalibration. Third, sensor dropout and telemetry delay can affect both the input features and the target labels. The preprocessing stage handles missing values, but imputation cannot recreate information that was never measured.",
    ))
    s.append(P(
        "The framework also assumes that the labels and the endurance target are generated consistently. In practice, the definition of solar-sufficient operation should specify the time window over which net battery drain is evaluated, and the endurance target should state the battery reserve or termination condition used in the simulation. These definitions matter at the boundary between classes. A future study should document them alongside the data-collection interval, sensor calibration procedure, and the treatment of repeated segments from the same flight.",
    ))
    s.append(P("D. Reproducibility and Evaluation Protocol", subsection))
    s.append(P(
        "A complete experimental version of this work should report enough information for another researcher to reconstruct the data pipeline. The description should identify whether each record originated from a physical flight or a MATLAB/Simulink simulation, give the logging interval, and state the ranges of irradiance, altitude, airspeed, payload, PV output, and battery voltage represented in the dataset. For physical measurements, the calibration status of the irradiance, voltage, current, and temperature sensors should also be recorded. These details are more informative than reporting a single aggregate score because they show which operating conditions the model has actually seen.",
    ))
    s.append(P(
        "Preprocessing must be fitted without using information from the testing flights. For example, imputation values, feature-scaling means, and standard deviations should be calculated from the training partition and then applied unchanged to the testing partition. If multiple rows are taken from one flight, the split should be performed at the flight or mission level whenever possible. This prevents nearly identical neighboring samples from appearing on both sides of the evaluation and provides a more conservative estimate of performance on a new sortie.",
    ))
    s.append(P(
        "The reporting protocol should also include the class distribution, the random seed or repetition procedure, the software environment, and the final hyperparameters. A confusion matrix would show whether the system is more likely to incorrectly declare a battery-assisted segment solar-sufficient or to incorrectly warn the operator that the battery is being used. That distinction has practical importance: a false solar-sufficient prediction may encourage an overly long route, whereas a false battery-assisted prediction is more likely to cause conservative planning. The present document retains the supplied accuracy, precision, recall, and F1-score values but does not infer missing confusion counts.",
    ))
    s.append(P(
        "For endurance, the final report should state the reserve threshold used to define the end of a flight, the construction of the prediction interval, and the number of independent missions in each test set. Results should be separated by payload and solar condition where the sample size permits. A simple energy-balance calculation can be included as an additional baseline, while the machine-learning models can be evaluated for whether they improve the estimate under changing conditions. This comparison would clarify whether the learned model contributes information beyond a direct physical estimate from PV power, load, and battery state.",
    ))
    s.append(P(
        "The intended dashboard accepts current irradiance, altitude, airspeed, and payload values from the operator or telemetry stream. The inputs are collected and preprocessed, the trained models are loaded, and the system returns a power-sufficiency label together with an endurance estimate. Representative dashboard frames are shown in Figs. 4-7.",
    ))
    s.append(KeepTogether([
        Table([
            [Image(str(IMAGE_DIR / "p6-img1.png"), width=COL_W/2-4, height=(COL_W/2-4)*416/302), Image(str(IMAGE_DIR / "p6-img2.png"), width=COL_W/2-4, height=(COL_W/2-4)*416/302)],
            [P("Fig. 4 (left) and Fig. 5 (right). Prediction results in dashboard (Frames 1-2).", caption), ""],
        ], colWidths=[COL_W/2, COL_W/2], hAlign="CENTER", style=TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 2), ("RIGHTPADDING", (0, 0), (-1, -1), 2),
            ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ])),
    ]))
    s.append(KeepTogether([
        Table([
            [Image(str(IMAGE_DIR / "p6-img3.png"), width=COL_W/2-4, height=(COL_W/2-4)*416/302), Image(str(IMAGE_DIR / "p6-img4.png"), width=COL_W/2-4, height=(COL_W/2-4)*416/302)],
            [P("Fig. 6 (left) and Fig. 7 (right). Prediction results in dashboard (Frames 3-4).", caption), ""],
        ], colWidths=[COL_W/2, COL_W/2], hAlign="CENTER", style=TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 2), ("RIGHTPADDING", (0, 0), (-1, -1), 2),
            ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ])),
    ]))
    s.append(P(
        "The dashboard output includes the predicted power-sufficiency mode and estimated remaining endurance for the current telemetry reading. A short status message is also intended to give the operator an immediate interpretation of whether the mission profile remains on track.",
    ))

    s.append(P("V. CONCLUSION", section))
    s.append(P(
        "Limited endurance remains a major constraint for multirotor UAV missions that require long or uninterrupted coverage. This paper presented a solar-hybrid estimation and adaptive power-allocation framework that combines four classifiers - LR, SVM, RF, and ANN - for solar-sufficiency mode prediction with a Linear Regression model for endurance estimation. A Kalman-filter state-of-charge estimator is used to provide a smoothed battery-state signal to the predictive models.",
    ))
    s.append(P(
        "For the illustrative classifier comparison, ANN achieved the strongest values across accuracy, precision, recall, and F1-score, with a maximum accuracy of 88.60%, followed by SVM. The endurance model achieved an average agreement of approximately 76% against the three simulated data sets in Table III. These results suggest that the proposed workflow can provide operators with a quantitative estimate of route feasibility before and during a mission. However, every reported value in the present paper is explicitly illustrative and must be replaced with the authors' measured flight-test or simulation results before the work is considered complete.",
    ))
    s.append(P(
        "Future work should validate the models using instrumented flight logs collected under different weather and payload conditions, extend the feature set with cloud cover and seasonal irradiance variables, and integrate the mode-dependent state-of-charge trajectory method of [14] into full-mission route planning. The authors should also document the construction of the endurance prediction interval and reconcile the small differences between the ANN metrics in Tables I and II.",
    ))

    s.append(P("REFERENCES", section))
    refs = [
        '[1] P. Saravanan, G. Ashwin, V. S. Arvinth, and V. Prasanna, "Solar Powered Quadcopter Drone," 2024 10th International Conference on Communication and Signal Processing (ICCSP), 2024, doi: 10.1109/ICCSP60870.2024.10544323.',
        '[2] "Solar-Powered UAVs: A Systematic Literature Review," IEEE Conference Publication, 2024, doi: 10.1109/document.10467158 (see IEEE Xplore document 10467158).',
        '[3] R. D\'Sa, D. Jenson, T. Henderson, J. Kilian, B. Schulz, M. Calvert, T. Heller, and N. Papanikolopoulos, "SUAV:Q - A Hybrid Approach to Solar-Powered Flight," IEEE/RSJ International Conference on Intelligent Robots and Systems (IROS), Daejeon, South Korea, Oct. 2016, pp. 1609-1615.',
        '[4] Hong Kong Polytechnic University capstone project, "Development of a Solar-Powered Unmanned Aerial Vehicle for Extended Flight Endurance," Drones, vol. 5, no. 2, p. 44, 2021, doi: 10.3390/drones5020044.',
        '[5] Center for Distributed Robotics, "Solar Powered UAV: Design and Experiments," IEEE Conference Publication, 2015, doi: 10.1109/document.7353711 (see IEEE Xplore document 7353711).',
        '[6] "Design and Development of a Small Solar-Powered UAV for Environmental Monitoring Application," IEEE Conference Publication, 2019, doi: 10.1109/document.8942777 (see IEEE Xplore document 8942777).',
        '[7] P. Oettershagen, A. Melzer, T. Mantel, K. Rudin, R. Lotz, D. Siebenmann, S. Leutenegger, K. Alexis, and R. Siegwart, "Perpetual Flight with a Small Solar-Powered UAV: Flight Results, Performance Analysis and Model Validation," IEEE Aerospace Conference, 2016, doi: 10.1109/document.7500855 (see IEEE Xplore document 7500855).',
        '[8] "Extending Flight Endurance of Electric Unmanned Aerial Vehicles through Photovoltaic System Integration," IEEE Conference Publication, 2015, doi: 10.1109/document.7418556 (see IEEE Xplore document 7418556).',
        '[9] S. J. Yawar, U. A. Usmani, M. Ali, M. Mahad, S. Ali, and F. Hussain, "Design and Control of Solar Quadcopter using RF and Arduino," Proceedings of the International Conference on Industrial Engineering and Operations Management, Singapore, 2021, doi: 10.46254/AN11.20210454.',
        '[10] R. Hariharan, S. Archana, D. Vijay, S. Srisathirapathy, M. Almusawi, and R. K. Jambi Ratna, "Advancements in Solar-Powered UAV Design Leveraging Machine Learning: A Comprehensive Review," E3S Web of Conferences, vol. 540, p. 02024, 2024, doi: 10.1051/e3sconf/202454002024.',
        '[11] K. N. Shah, P. Prajapati, and J. M. Karandikar, "Design and Development of Solar Drone," International Journal for Research in Applied Science & Engineering Technology (IJRASET), vol. 11, no. 6, Jun. 2023.',
        '[12] A. Abidali, S. A. Agha, A. Munjiza, and M. H. Shaheed, "Development of a Solar Powered Multirotor Micro Aerial Vehicle," Scientific Reports, vol. 14, no. 1, pp. 1-12, 2024, doi: 10.1038/s41598-024-54079-9.',
        '[13] R. Elagib and A. Karaarslan, "Modeling and Simulation of a Solar-Powered Quadcopter Using MATLAB," in Drones - Various Applications, IntechOpen, 2023, doi: 10.5772/intechopen.1002671.',
        '[14] X. Cao and L. Liu, "State-of-Charge Trajectory Planning for Low-Altitude Solar-Powered Convertible UAV by Driven Modes," Drones, vol. 8, no. 3, p. 80, 2024.',
    ]
    for ref in refs:
        s.append(P(ref, reference))
    return s


if __name__ == "__main__":
    doc = IEEEReport(str(OUTPUT_PDF), title="Solar-Powered Hybrid Drone for Extended Flight Duration", author="Lok Adhithya G; Arokya Suman Raj; Dr. A. Muthulakshmi")
    doc.build(story())
    print(OUTPUT_PDF)
