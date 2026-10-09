using PriestsAndDevils.Controllers;
using PriestsAndDevils.Models;
using UnityEngine;

namespace PriestsAndDevils.Views
{
    public sealed class Person3DView : MonoBehaviour
    {
        private GameController3D controller;
        private int personId;
        private Renderer meshRenderer;
        private Color baseColor;

        public void Initialize(GameController3D owner, PersonModel person, Color color)
        {
            controller = owner;
            personId = person.Id;
            baseColor = color;
            meshRenderer = GetComponent<Renderer>();
            if (meshRenderer == null) meshRenderer = gameObject.AddComponent<MeshRenderer>();
            ApplyColor(meshRenderer, baseColor);
            if (GetComponent<Collider>() == null) gameObject.AddComponent<BoxCollider>();
        }

        public void SetSelected(bool selected)
        {
            if (meshRenderer != null) ApplyColor(meshRenderer, selected ? Color.yellow : baseColor);
        }

        private void ApplyColor(Renderer renderer, Color color)
        {
            Shader shader = Shader.Find("Unlit/Color");
            if (shader == null) shader = Shader.Find("Standard");
            if (shader != null && renderer.material.shader != shader) renderer.material = new Material(shader);
            renderer.material.color = color;
        }

        private void OnMouseDown()
        {
            controller?.OnPersonClicked(personId);
        }
    }
}
