using System.Collections.Generic;
using PriestsAndDevils.Controllers;
using PriestsAndDevils.Models;
using UnityEngine;
using UnityEngine.UI;

namespace PriestsAndDevils.Views
{
    public sealed class GameView
    {
        private readonly GameController controller;
        private readonly Transform root;
        private readonly Dictionary<int, PersonView> personViews = new Dictionary<int, PersonView>();
        private readonly Dictionary<int, Vector3> leftPositions = new Dictionary<int, Vector3>
        {
            { 0, new Vector3(-6.0f, -0.9f, -1) }, { 1, new Vector3(-5.0f, -0.9f, -1) }, { 2, new Vector3(-4.0f, -0.9f, -1) },
            { 3, new Vector3(-6.0f, 0.1f, -1) }, { 4, new Vector3(-5.0f, 0.1f, -1) }, { 5, new Vector3(-4.0f, 0.1f, -1) }
        };
        private readonly Dictionary<int, Vector3> rightPositions = new Dictionary<int, Vector3>
        {
            { 0, new Vector3(4.0f, -0.9f, -1) }, { 1, new Vector3(5.0f, -0.9f, -1) }, { 2, new Vector3(6.0f, -0.9f, -1) },
            { 3, new Vector3(4.0f, 0.1f, -1) }, { 4, new Vector3(5.0f, 0.1f, -1) }, { 5, new Vector3(6.0f, 0.1f, -1) }
        };
        private BoatView boatView;
        private Text statusText;
        private Text timerText;
        private Text leftCountText;
        private Text rightCountText;
        private Button goButton;
        private Button restartButton;

        public GameView(GameController owner)
        {
            controller = owner;
            root = new GameObject("RuntimeView").transform;
        }

        public void Build(GameModel model)
        {
            CreateWorld();
            CreateUi();
            foreach (PersonModel person in model.Persons)
            {
                PrimitiveType shape = person.Type == PersonType.Priest ? PrimitiveType.Cube : PrimitiveType.Sphere;
                GameObject objectRoot = GameObject.CreatePrimitive(shape);
                objectRoot.name = person.Type + "_" + person.Id;
                objectRoot.transform.SetParent(root);
                PersonView view = objectRoot.GetComponent<PersonView>();
                if (view == null) view = objectRoot.AddComponent<PersonView>();
                view.Initialize(controller, person, person.Type == PersonType.Priest ? Color.white : new Color(0.92f, 0.12f, 0.16f));
                personViews.Add(person.Id, view);
            }

            GameObject boatObject = GameObject.CreatePrimitive(PrimitiveType.Cube);
            boatObject.name = "Boat";
            boatObject.transform.SetParent(root);
            boatView = boatObject.GetComponent<BoatView>();
            if (boatView == null) boatView = boatObject.AddComponent<BoatView>();
            boatView.Initialize(new Color(0.38f, 0.18f, 0.08f));
            boatObject.transform.localScale = new Vector3(2.2f, 0.55f, 1);
            Refresh(model);
        }

        public void Refresh(GameModel model)
        {
            foreach (PersonModel person in model.Persons)
            {
                PersonView view = personViews[person.Id];
                Transform transform = view.transform;
                if (person.InBoat)
                {
                    int index = model.Boat.PassengerIds.IndexOf(person.Id);
                    float xOffset = index == 0 ? -0.48f : 0.48f;
                    transform.position = boatView.transform.position + new Vector3(xOffset, 0.65f, -0.5f);
                    transform.localScale = Vector3.one * 0.58f;
                    view.SetSelected(true);
                }
                else
                {
                    transform.position = person.Side == Side.Left ? leftPositions[person.Id] : rightPositions[person.Id];
                    transform.localScale = Vector3.one * 0.75f;
                    view.SetSelected(false);
                }
            }

            boatView.transform.position = model.Boat.Side == Side.Left ? new Vector3(-1.6f, -2.45f, -1) : new Vector3(1.6f, -2.45f, -1);
            leftCountText.text = "LEFT  " + Count(model, Side.Left);
            rightCountText.text = "RIGHT  " + Count(model, Side.Right);
            timerText.text = "TIME LEFT  " + model.RemainingSeconds.ToString("00");
            goButton.interactable = model.Status == GameStatus.Playing && !model.Boat.IsMoving;
            string status = model.Status == GameStatus.Playing ? "选择船所在岸边的角色，再点击 GO" : (model.Status == GameStatus.Won ? "胜利！所有人安全到达右岸" : "游戏结束：" + (model.RemainingSeconds == 0 ? "时间到了" : "牧师被恶魔吃掉了"));
            statusText.text = status;
        }

        public void SetBoatPosition(Vector3 position)
        {
            if (boatView != null) boatView.transform.position = position;
        }

        private int Count(GameModel model, Side side)
        {
            int count = 0;
            foreach (PersonModel person in model.Persons) if (!person.InBoat && person.Side == side) count++;
            return count;
        }

        private void CreateWorld()
        {
            CreateBlock("Sky", new Vector3(0, 0.4f, 2), new Vector2(40, 20f), new Color(0.035f, 0.12f, 0.22f), -8);
            CreateBlock("Water", new Vector3(0, -3.25f, 1), new Vector2(40, 1.9f), new Color(0.04f, 0.42f, 0.67f), -7);
            CreateBlock("LeftIsland", new Vector3(-6.1f, -2.25f, 0), new Vector2(5.2f, 1.7f), new Color(0.29f, 0.56f, 0.22f), -6);
            CreateBlock("RightIsland", new Vector3(6.1f, -2.25f, 0), new Vector2(5.2f, 1.7f), new Color(0.29f, 0.56f, 0.22f), -6);
            CreateBlock("LeftShore", new Vector3(-6.1f, -2.75f, -0.2f), new Vector2(5.2f, 0.5f), new Color(0.63f, 0.48f, 0.22f), -5);
            CreateBlock("RightShore", new Vector3(6.1f, -2.75f, -0.2f), new Vector2(5.2f, 0.5f), new Color(0.63f, 0.48f, 0.22f), -5);
        }

        private GameObject CreateBlock(string name, Vector3 position, Vector2 size, Color color, int order)
        {
            GameObject block = GameObject.CreatePrimitive(PrimitiveType.Cube);
            block.name = name;
            block.transform.SetParent(root);
            block.transform.position = position;
            block.transform.localScale = new Vector3(size.x, size.y, 0.1f);
            MeshRenderer renderer = block.GetComponent<MeshRenderer>();
            if (renderer == null) renderer = block.AddComponent<MeshRenderer>();
            Shader unlit = Shader.Find("Unlit/Color");
            if (unlit != null) renderer.material.shader = unlit;
            renderer.material.color = color;
            return block;
        }

        private void CreateUi()
        {
            GameObject canvasObject = new GameObject("HUD");
            canvasObject.transform.SetParent(root);
            Canvas canvas = canvasObject.AddComponent<Canvas>();
            canvas.renderMode = RenderMode.ScreenSpaceOverlay;
            CanvasScaler scaler = canvasObject.AddComponent<CanvasScaler>();
            scaler.uiScaleMode = CanvasScaler.ScaleMode.ScaleWithScreenSize;
            scaler.referenceResolution = new Vector2(1280, 720);
            scaler.screenMatchMode = CanvasScaler.ScreenMatchMode.MatchWidthOrHeight;
            scaler.matchWidthOrHeight = 0.5f;
            canvasObject.AddComponent<GraphicRaycaster>();

            Text title = CreateText(canvasObject.transform, "PRIESTS  &  DEVILS", 28, new Color(0.86f, 0.93f, 1f), new Vector2(0.5f, 0.93f), new Vector2(500, 55));
            title.fontStyle = FontStyle.Bold;
            timerText = CreateText(canvasObject.transform, "TIME LEFT  120", 23, new Color(0.76f, 0.91f, 1f), new Vector2(0.86f, 0.93f), new Vector2(290, 50));
            leftCountText = CreateText(canvasObject.transform, "LEFT  6", 18, Color.white, new Vector2(0.18f, 0.72f), new Vector2(180, 42));
            rightCountText = CreateText(canvasObject.transform, "RIGHT  0", 18, Color.white, new Vector2(0.82f, 0.72f), new Vector2(180, 42));
            statusText = CreateText(canvasObject.transform, "选择船所在岸边的角色，再点击 GO", 18, new Color(0.85f, 0.9f, 0.96f), new Vector2(0.5f, 0.11f), new Vector2(700, 45));

            goButton = CreateButton(canvasObject.transform, "GO", new Vector2(0.5f, 0.19f), new Vector2(150, 58), new Color(0.46f, 0.16f, 0.64f), controller.OnGoClicked);
            restartButton = CreateButton(canvasObject.transform, "RESTART", new Vector2(0.11f, 0.93f), new Vector2(190, 48), new Color(0.08f, 0.28f, 0.47f), controller.OnRestartClicked);
        }

        private Text CreateText(Transform parent, string content, int size, Color color, Vector2 anchor, Vector2 dimensions)
        {
            GameObject objectRoot = new GameObject("Text_" + content);
            objectRoot.transform.SetParent(parent);
            RectTransform rect = objectRoot.AddComponent<RectTransform>();
            rect.anchorMin = anchor; rect.anchorMax = anchor; rect.pivot = new Vector2(0.5f, 0.5f); rect.sizeDelta = dimensions;
            Text text = objectRoot.AddComponent<Text>();
            text.text = content; text.font = Resources.GetBuiltinResource<Font>("LegacyRuntime.ttf"); text.fontSize = size; text.color = color;
            text.alignment = TextAnchor.MiddleCenter; text.horizontalOverflow = HorizontalWrapMode.Overflow; text.verticalOverflow = VerticalWrapMode.Overflow;
            return text;
        }

        private Button CreateButton(Transform parent, string label, Vector2 anchor, Vector2 dimensions, Color color, UnityEngine.Events.UnityAction action)
        {
            GameObject objectRoot = new GameObject("Button_" + label);
            objectRoot.transform.SetParent(parent);
            RectTransform rect = objectRoot.AddComponent<RectTransform>();
            rect.anchorMin = anchor; rect.anchorMax = anchor; rect.pivot = new Vector2(0.5f, 0.5f); rect.sizeDelta = dimensions;
            Image image = objectRoot.AddComponent<Image>(); image.color = color;
            Button button = objectRoot.AddComponent<Button>(); button.onClick.AddListener(action);
            Text text = CreateText(objectRoot.transform, label, 22, Color.white, new Vector2(0.5f, 0.5f), dimensions);
            text.fontStyle = FontStyle.Bold;
            return button;
        }
    }
}
