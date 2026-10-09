using System.Collections.Generic;
using PriestsAndDevils.Controllers;
using PriestsAndDevils.Models;
using UnityEngine;
using UnityEngine.UI;

namespace PriestsAndDevils.Views
{
    public sealed class Game3DView
    {
        private readonly GameController3D controller;
        private readonly Transform root;
        private readonly Dictionary<int, Person3DView> personViews = new Dictionary<int, Person3DView>();
        private readonly Dictionary<int, Vector3> leftPositions = new Dictionary<int, Vector3>
        {
            { 0, new Vector3(-7.6f, 0.9f, -1.3f) }, { 1, new Vector3(-6.5f, 0.9f, -1.3f) }, { 2, new Vector3(-5.4f, 0.9f, -1.3f) },
            { 3, new Vector3(-7.6f, 0.9f, 1.3f) }, { 4, new Vector3(-6.5f, 0.9f, 1.3f) }, { 5, new Vector3(-5.4f, 0.9f, 1.3f) }
        };
        private readonly Dictionary<int, Vector3> rightPositions = new Dictionary<int, Vector3>
        {
            { 0, new Vector3(5.4f, 0.9f, -1.3f) }, { 1, new Vector3(6.5f, 0.9f, -1.3f) }, { 2, new Vector3(7.6f, 0.9f, -1.3f) },
            { 3, new Vector3(5.4f, 0.9f, 1.3f) }, { 4, new Vector3(6.5f, 0.9f, 1.3f) }, { 5, new Vector3(7.6f, 0.9f, 1.3f) }
        };
        private GameModel model;
        private GameObject boatObject;
        private Text statusText;
        private Text timerText;
        private Text leftCountText;
        private Text rightCountText;
        private Button goButton;

        public Game3DView(GameController3D owner)
        {
            controller = owner;
            root = new GameObject("Runtime3DView").transform;
        }

        public void Build(GameModel gameModel)
        {
            model = gameModel;
            CreateWorld();
            CreateUi();

            foreach (PersonModel person in model.Persons)
            {
                PrimitiveType shape = person.Type == PersonType.Priest ? PrimitiveType.Cube : PrimitiveType.Sphere;
                GameObject objectRoot = GameObject.CreatePrimitive(shape);
                objectRoot.name = person.Type + "_" + person.Id;
                objectRoot.transform.SetParent(root);
                Person3DView view = objectRoot.AddComponent<Person3DView>();
                view.Initialize(controller, person, person.Type == PersonType.Priest ? Color.white : new Color(0.92f, 0.08f, 0.1f));
                personViews.Add(person.Id, view);
            }

            boatObject = GameObject.CreatePrimitive(PrimitiveType.Cube);
            boatObject.name = "Boat";
            boatObject.transform.SetParent(root);
            boatObject.transform.localScale = new Vector3(2.6f, 0.45f, 1.8f);
            Boat3DView boatView = boatObject.AddComponent<Boat3DView>();
            boatView.Initialize(new Color(0.36f, 0.14f, 0.045f));
            Refresh(model);
        }

        public void Refresh(GameModel gameModel)
        {
            model = gameModel;
            boatObject.transform.position = model.Boat.Side == Side.Left ? new Vector3(-1.9f, 0.55f, 0) : new Vector3(1.9f, 0.55f, 0);
            foreach (PersonModel person in model.Persons)
            {
                Person3DView view = personViews[person.Id];
                if (person.InBoat)
                {
                    view.transform.position = PassengerPosition(model.Boat.PassengerIds.IndexOf(person.Id));
                    view.transform.localScale = Vector3.one * 0.72f;
                    view.SetSelected(true);
                }
                else
                {
                    view.transform.position = person.Side == Side.Left ? leftPositions[person.Id] : rightPositions[person.Id];
                    view.transform.localScale = Vector3.one * 0.82f;
                    view.SetSelected(false);
                }
            }

            leftCountText.text = "LEFT  " + Count(Side.Left);
            rightCountText.text = "RIGHT  " + Count(Side.Right);
            timerText.text = "TIME LEFT  " + model.RemainingSeconds.ToString("00");
            goButton.interactable = model.Status == GameStatus.Playing && !model.Boat.IsMoving;
            statusText.text = model.Status == GameStatus.Playing ? "Click 1-2 people, then press GO" : (model.Status == GameStatus.Won ? "YOU WIN - EVERYONE IS SAFE" : "GAME OVER - PRESS RESTART");
        }

        public void SetBoatPosition(Vector3 position)
        {
            boatObject.transform.position = position;
            foreach (PersonModel person in model.Persons)
            {
                if (person.InBoat) personViews[person.Id].transform.position = PassengerPosition(model.Boat.PassengerIds.IndexOf(person.Id));
            }
        }

        private Vector3 PassengerPosition(int index)
        {
            return boatObject.transform.position + new Vector3(index == 0 ? -0.5f : 0.5f, 0.85f, 0);
        }

        private int Count(Side side)
        {
            int count = 0;
            foreach (PersonModel person in model.Persons) if (!person.InBoat && person.Side == side) count++;
            return count;
        }

        private void CreateWorld()
        {
            CreateBlock("Water", new Vector3(0, -0.25f, 0), new Vector3(4.1f, 0.3f, 8.5f), new Color(0.02f, 0.3f, 0.58f));
            CreateBlock("LeftIsland", new Vector3(-6.6f, 0, 0), new Vector3(5.2f, 0.8f, 8.5f), new Color(0.23f, 0.5f, 0.18f));
            CreateBlock("RightIsland", new Vector3(6.6f, 0, 0), new Vector3(5.2f, 0.8f, 8.5f), new Color(0.23f, 0.5f, 0.18f));
            CreateBlock("LeftShore", new Vector3(-3.95f, 0.45f, 0), new Vector3(0.35f, 0.18f, 8.5f), new Color(0.64f, 0.48f, 0.2f));
            CreateBlock("RightShore", new Vector3(3.95f, 0.45f, 0), new Vector3(0.35f, 0.18f, 8.5f), new Color(0.64f, 0.48f, 0.2f));

            GameObject lightObject = new GameObject("RuntimeSun");
            lightObject.transform.SetParent(root);
            lightObject.transform.rotation = Quaternion.Euler(48, -28, 0);
            Light light = lightObject.AddComponent<Light>();
            light.type = LightType.Directional;
            light.intensity = 1.15f;
            RenderSettings.ambientLight = new Color(0.25f, 0.3f, 0.4f);
        }

        private GameObject CreateBlock(string name, Vector3 position, Vector3 scale, Color color)
        {
            GameObject block = GameObject.CreatePrimitive(PrimitiveType.Cube);
            block.name = name;
            block.transform.SetParent(root);
            block.transform.position = position;
            block.transform.localScale = scale;
            Renderer renderer = block.GetComponent<Renderer>();
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

            Text title = CreateText(canvasObject.transform, "PRIESTS  &  DEVILS  3D", 28, new Color(0.85f, 0.93f, 1), new Vector2(0.5f, 0.93f), new Vector2(620, 55));
            title.fontStyle = FontStyle.Bold;
            timerText = CreateText(canvasObject.transform, "TIME LEFT  120", 22, new Color(0.8f, 0.92f, 1), new Vector2(0.84f, 0.93f), new Vector2(260, 46));
            leftCountText = CreateText(canvasObject.transform, "LEFT  6", 18, Color.white, new Vector2(0.19f, 0.72f), new Vector2(180, 40));
            rightCountText = CreateText(canvasObject.transform, "RIGHT  0", 18, Color.white, new Vector2(0.81f, 0.72f), new Vector2(180, 40));
            statusText = CreateText(canvasObject.transform, "Click 1-2 people, then press GO", 18, Color.white, new Vector2(0.5f, 0.09f), new Vector2(700, 42));
            goButton = CreateButton(canvasObject.transform, "GO", new Vector2(0.5f, 0.18f), new Vector2(150, 56), new Color(0.45f, 0.15f, 0.65f), controller.OnGoClicked);
            CreateButton(canvasObject.transform, "RESTART", new Vector2(0.12f, 0.93f), new Vector2(190, 46), new Color(0.07f, 0.28f, 0.48f), controller.OnRestartClicked);
        }

        private Text CreateText(Transform parent, string content, int size, Color color, Vector2 anchor, Vector2 dimensions)
        {
            GameObject textObject = new GameObject("Text_" + content);
            textObject.transform.SetParent(parent);
            RectTransform rect = textObject.AddComponent<RectTransform>();
            rect.anchorMin = anchor; rect.anchorMax = anchor; rect.pivot = new Vector2(0.5f, 0.5f); rect.sizeDelta = dimensions;
            Text text = textObject.AddComponent<Text>();
            text.text = content;
            text.font = Resources.GetBuiltinResource<Font>("LegacyRuntime.ttf");
            text.fontSize = size; text.color = color; text.alignment = TextAnchor.MiddleCenter;
            text.horizontalOverflow = HorizontalWrapMode.Overflow; text.verticalOverflow = VerticalWrapMode.Overflow;
            return text;
        }

        private Button CreateButton(Transform parent, string label, Vector2 anchor, Vector2 dimensions, Color color, UnityEngine.Events.UnityAction action)
        {
            GameObject buttonObject = new GameObject("Button_" + label);
            buttonObject.transform.SetParent(parent);
            RectTransform rect = buttonObject.AddComponent<RectTransform>();
            rect.anchorMin = anchor; rect.anchorMax = anchor; rect.pivot = new Vector2(0.5f, 0.5f); rect.sizeDelta = dimensions;
            Image image = buttonObject.AddComponent<Image>(); image.color = color;
            Button button = buttonObject.AddComponent<Button>(); button.onClick.AddListener(action);
            Text text = CreateText(buttonObject.transform, label, 22, Color.white, new Vector2(0.5f, 0.5f), dimensions);
            text.fontStyle = FontStyle.Bold;
            return button;
        }
    }
}
